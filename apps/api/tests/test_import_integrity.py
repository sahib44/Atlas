import csv
import io
import os
from uuid import UUID

import pytest
from sqlalchemy import event, func, select
from sqlalchemy.exc import DBAPIError

from mist.db import SessionLocal, engine, now
from mist.models import Audit, Engagement, Judge, JudgeCategory, JudgeQualification
from test_workflows import ok


def analyze(client, content, mapping=None):
    batch = ok(client.post("/api/imports", files={"file": ("fabricated.csv", content, "text/csv")}))
    path = f"/api/imports/{batch['id']}"
    ok(client.put(f"{path}/mapping", json={"mapping": mapping or batch["mapping"]}))
    return ok(client.post(f"{path}/analyze"))


def commit(client, batch):
    return ok(client.post(f"/api/imports/{batch['id']}/commit", json={"confirmed": True}))


def candidates(client, batch):
    return ok(client.get(f"/api/duplicates?batch_id={batch['id']}"))["items"]


def resolve(client, candidate, decision="merge"):
    return client.post(f"/api/duplicates/{candidate['id']}/resolve", json={
        "decision": decision, "confirmed": True,
    })


def contact(client, name, address, **extra):
    return ok(client.post("/api/judges", json={
        "display_name": name, "primary_email": address, **extra,
    }))


def merge(client, survivor, former):
    return ok(client.post(f"/api/judges/{survivor['id']}/merge", json={
        "merged_judge_id": former["id"], "confirmed": True, "field_decisions": {},
    }))


def test_import_detects_preserved_addresses_and_refuses_creating_exact_duplicates(director):
    survivor = contact(director, "Inez Marlow", "inez@example.org",
                       alternate_email="inez.current@example.org")
    former = contact(director, "Inez Marlowe", "inez.former@example.org",
                     alternate_email="inez.alias@example.org", do_not_contact=True)
    merge(director, survivor, former)
    batch = analyze(director, "Full Name,Email\nUnrelated Name, INEZ.ALIAS@EXAMPLE.ORG \n")
    [match] = candidates(director, batch)
    assert match["existing_judge_id"] == survivor["id"] and match["confidence_score"] >= 0.98
    assert resolve(director, match, "create").status_code == 409
    ok(resolve(director, match))
    assert commit(director, batch)["updated_count"] == 1
    updated = ok(director.get(f"/api/judges/{survivor['id']}"))
    assert updated["do_not_contact"] and "inez.alias@example.org" in updated["preserved_emails"]


def test_ambiguous_addresses_cannot_be_absorbed_into_one_of_two_contacts(director):
    first = contact(director, "Inez Marlow", "inez@example.org")
    second = contact(director, "Aditi Das", "aditi@example.org")
    batch = analyze(director, "Full Name,Email,Alternate Email\nInez Marlow,inez@example.org,aditi@example.org\n")
    matches = candidates(director, batch)
    assert {match["existing_judge_id"] for match in matches} == {first["id"], second["id"]}
    for match in matches:
        assert resolve(director, match).status_code == 409
    ok(resolve(director, matches[0], "skip"))
    assert commit(director, batch)["skipped_count"] == 1
    assert ok(director.get(f"/api/judges/{first['id']}"))["alternate_email"] == ""


def test_manual_contact_writes_refuse_another_contacts_alias(director):
    owner = contact(director, "Inez Marlow", "inez@example.org", alternate_email="inez.alias@example.org")
    other = contact(director, "Aditi Das", "aditi@example.org")
    data = {"display_name": other["display_name"], "primary_email": " INEZ.ALIAS@EXAMPLE.ORG "}
    assert director.post("/api/judges", json=data).status_code == 409
    assert director.patch(f"/api/judges/{other['id']}", json=data).status_code == 409
    assert ok(director.get(f"/api/judges/{other['id']}"))["primary_email"] == "aditi@example.org"
    assert ok(director.get(f"/api/judges/{owner['id']}"))["alternate_email"] == "inez.alias@example.org"


def test_within_file_keep_separate_cannot_create_a_shared_alias(director):
    batch = analyze(director, "Full Name,Email,Alternate Email\nInez Marlow,inez@example.org,shared@example.org\nMalik Mensah,shared@example.org,malik@example.org\n")
    [match] = candidates(director, batch)
    assert match["existing_row_id"]
    ok(resolve(director, match, "create"))
    result = commit(director, batch)
    assert (result["created_count"], result["skipped_count"]) == (1, 1)
    assert "already belongs" in director.get(f"/api/imports/{batch['id']}/errors").text


def test_commit_rechecks_stale_analysis_and_audits_rejected_rows(director):
    earlier = analyze(director, "Full Name,Email,Notes\nInez Marlow,inez@example.org,Original\n")
    later = analyze(director, "Full Name,Email,Notes\nInez Marlow,inez@example.org,Conflicting\nMalik Mensah,malik@example.org,Independent\n")
    assert commit(director, earlier)["created_count"] == 1
    result = commit(director, later)
    assert (result["created_count"], result["skipped_count"]) == (1, 1)
    assert commit(director, later) == result
    rows = ok(director.get("/api/judges?q=inez@example.org"))["items"]
    assert len(rows) == 1 and rows[0]["notes"] == "Original"
    assert "already belongs" in director.get(f"/api/imports/{later['id']}/errors").text
    with SessionLocal() as db:
        assert db.scalar(select(func.count()).select_from(Audit).where(Audit.action == "import.row_rejected")) == 1


def test_same_file_review_twice_preserves_multiline_notes_once(director):
    first = 'Full Name,Email,Notes\nInez Marlow,inez@example.org,"Fiber & art\nSecond line"\n'
    second = 'Full Name,Email,Notes\nInez Marlow,inez@example.org,"Other line\nAnother line"\n'
    assert commit(director, analyze(director, first))["created_count"] == 1
    for content in (first, second, second):
        batch = analyze(director, content)
        [match] = candidates(director, batch)
        ok(resolve(director, match))
        assert commit(director, batch)["updated_count"] == 1
    [person] = ok(director.get("/api/judges?q=inez@example.org"))["items"]
    assert person["notes"] == "Fiber &amp; art\nSecond line\n\nOther line\nAnother line"


def test_repeated_name_only_import_requires_explicit_review(director):
    content = "Name\nInez Marlow\n"
    assert commit(director, analyze(director, content))["created_count"] == 1
    repeated = analyze(director, content)
    [match] = candidates(director, repeated)
    assert match["match_type"] == "name_only"
    assert director.post(f"/api/imports/{repeated['id']}/commit", json={"confirmed": True}).status_code == 400
    ok(resolve(director, match))
    assert commit(director, repeated)["updated_count"] == 1
    assert ok(director.get("/api/judges?q=Inez%20Marlow"))["total"] == 1


def test_commit_rechecks_stale_name_only_analysis(director):
    content = "Name\nInez Marlow\n"
    earlier, later = analyze(director, content), analyze(director, content)
    assert earlier["duplicate_count"] == later["duplicate_count"] == 0
    assert commit(director, earlier)["created_count"] == 1
    result = commit(director, later)
    assert (result["created_count"], result["skipped_count"]) == (0, 1)
    assert "name now matches" in director.get(f"/api/imports/{later['id']}/errors").text
    assert commit(director, later) == result
    assert ok(director.get("/api/judges?q=Inez%20Marlow"))["total"] == 1


@pytest.mark.parametrize(("name", "stored_name"), [
    ("<strong>Inez   Marlow</strong>", "Inez Marlow"),
    ("Inez   & Marlow", "Inez &amp; Marlow"),
])
def test_name_only_reimport_compares_the_saved_name(director, name, stored_name):
    content = f"Name\n{name}\n"
    assert commit(director, analyze(director, content))["created_count"] == 1
    repeated = analyze(director, content)
    [match] = candidates(director, repeated)
    assert match["match_type"] == "name_only" and match["confidence_score"] >= 0.7
    details = ok(director.get(f"/api/duplicates/{match['id']}"))
    assert details["existing"]["display_name"] == stored_name
    assert director.post(f"/api/imports/{repeated['id']}/commit", json={"confirmed": True}).status_code == 400


def test_commit_preserves_explicit_keep_separate_for_reviewed_names(director):
    contact(director, "Inez Marlow", "")
    batch = analyze(director, "Name\nInez Marlow\n")
    [match] = candidates(director, batch)
    ok(resolve(director, match, "create"))
    assert commit(director, batch)["created_count"] == 1
    assert ok(director.get("/api/judges?q=Inez%20Marlow"))["total"] == 2


def test_name_matching_handles_whitespace_in_existing_contacts(director):
    existing = contact(director, "Inez  Marlow", "")
    assert existing["display_name"] == "Inez  Marlow"
    batch = analyze(director, "Name\nInez Marlow\n")
    [match] = candidates(director, batch)
    assert match["existing_judge_id"] == existing["id"] and match["confidence_score"] >= 0.7


def test_late_email_conflict_marks_pending_match_rejected(director):
    contact(director, "Inez Marlow", "inez-original@example.org")
    batch = analyze(director, "Name,Email\nInez Marlow,inez@example.org\n")
    [match] = candidates(director, batch)
    assert match["match_type"] == "name_only" and match["confidence_score"] < 0.7
    assert batch["duplicate_count"] == 0
    contact(director, "Aditi Das", "inez@example.org")
    result = commit(director, batch)
    assert (result["created_count"], result["skipped_count"]) == (0, 1)
    assert "already belongs" in director.get(f"/api/imports/{batch['id']}/errors").text
    assert ok(director.get(f"/api/duplicates/{match['id']}"))["resolution"] == "rejected"


def test_alternate_only_mapping_and_invalid_values(director):
    content = "Alternate Email,Do Not Contact,Years\n ALIAS@EXAMPLE.ORG ,yes,2025\n,yes,2025\nbad-email,no,2025\nother@example.org,typo,2025\nyear@example.org,false,yesterday\n"
    batch = analyze(director, content)
    assert ok(director.post(f"/api/imports/{batch['id']}/analyze")) == batch
    assert ok(director.get(f"/api/imports/{batch['id']}"))["counts"] == {"create": 1, "invalid": 4}
    assert commit(director, batch)["created_count"] == 1
    with SessionLocal() as db:
        judge = db.scalar(select(Judge).where(Judge.alternate_email == "alias@example.org"))
        assert judge.display_name == "alias@example.org" and judge.do_not_contact


def test_database_failure_rolls_back_prior_rows_and_retry_is_safe(director):
    batch = analyze(director, "Full Name,Email\nInez Marlow,inez@example.org\nMalik Mensah,malik@example.org\n")
    inserts = 0

    def fail_second_insert(conn, cursor, statement, parameters, context, executemany):
        nonlocal inserts
        if statement.startswith("INSERT INTO judge_contacts"):
            inserts += 1
            if inserts == 2:
                statement = "SELECT 1 / 0"
        return statement, parameters

    event.listen(engine, "before_cursor_execute", fail_second_insert, retval=True)
    try:
        with pytest.raises(DBAPIError):
            commit(director, batch)
    finally:
        event.remove(engine, "before_cursor_execute", fail_second_insert)
    assert inserts == 2
    assert ok(director.get(f"/api/imports/{batch['id']}"))["counts"] == {"create": 2}
    assert ok(director.get("/api/judges?q=inez@example.org"))["total"] == 0
    with SessionLocal() as db:
        assert not db.scalar(select(Audit.id).where(Audit.action == "import.committed"))
    result = commit(director, batch)
    assert result["created_count"] == 2
    assert commit(director, batch) == result


def test_chained_merge_preserves_relationship_details_and_existing_engagements(director):
    survivor = contact(director, "Inez Marlow", "inez@example.org")
    middle = contact(director, "Aditi Das", "aditi@example.org")
    former = contact(director, "Malik Mensah", "malik@example.org",
                     qualifications=["Fiber art"], categories=["Visual Arts"], years=[2025])
    former_id = UUID(former["id"])
    with SessionLocal.begin() as db:
        db.get(Judge, former_id).last_contacted_at = now()
        db.scalar(select(JudgeQualification).where(JudgeQualification.judge_id == former_id)).proficiency = "Expert"
        db.scalar(select(JudgeCategory).where(JudgeCategory.judge_id == former_id)).eligibility_status = "conditional"
        engagement = db.scalar(select(Engagement).where(Engagement.judge_id == former_id))
        engagement.notes = "Original engagement"
        engagement_id = engagement.id
    merge(director, middle, former)
    result = merge(director, survivor, middle)
    assert result["last_contacted_at"]
    assert ok(director.get(f"/api/judges/{survivor['id']}/history?kind=merges"))["total"] == 2
    survivor_id = UUID(survivor["id"])
    with SessionLocal() as db:
        assert db.scalar(select(JudgeQualification).where(JudgeQualification.judge_id == survivor_id)).proficiency == "Expert"
        assert db.scalar(select(JudgeCategory).where(JudgeCategory.judge_id == survivor_id)).eligibility_status == "conditional"
        [engagement] = db.scalars(select(Engagement).where(Engagement.judge_id == survivor_id)).all()
        assert engagement.id == engagement_id and engagement.notes == "Original engagement"


def test_error_csv_keeps_original_columns_named_like_diagnostics(director):
    batch = analyze(director, "Email,row_number,errors\ninvalid,legacy key,Original notes\n")
    response = director.get(f"/api/imports/{batch['id']}/errors")
    [row] = csv.DictReader(io.StringIO(response.text))
    assert row["row_number"] == "2" and "valid email" in row["errors"]
    assert {"legacy key", "Original notes"} <= set(row.values())


def test_duplicate_review_recovers_after_an_explicit_directory_clear(director):
    contact(director, "Inez Marlow", "inez@example.org")
    batch = analyze(director, "Name,Email\nInez Marlow,inez@example.org\n")
    [match] = candidates(director, batch)
    backup = ok(director.post("/api/judge-directory/clear/prepare"))
    ok(director.post("/api/judge-directory/clear", json={
        "confirmed_first": True, "confirmed_second": True,
        "password": os.environ["SEED_PASSWORD"], "backup_id": backup["id"],
    }))
    assert ok(director.get(f"/api/duplicates/{match['id']}"))["existing"] == {}
    assert resolve(director, match).status_code == 409
    ok(resolve(director, match, "skip"))
    assert commit(director, batch)["skipped_count"] == 1
