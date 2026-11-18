from concurrent.futures import ThreadPoolExecutor
from threading import Barrier
from uuid import UUID, uuid4

import pytest
from fastapi.testclient import TestClient

from mist.db import SessionLocal
from mist.main import app
from mist.models import Judge, PilotNomination
from mist.worker import run_once
from test_official_assignments import category, confirm, nominate, pilot
from test_recruitment import competition, coverage, draft, login, ok, payload, respond, review, submit
from test_workflows import queue


def test_same_year_assignment_does_not_hide_other_competition_request(director):
    first, second = competition(director), competition(director, "3D Art")
    candidate = submit(director, first)
    review(director, candidate)
    respond(director, candidate, "accepted")
    assignment = ok(director.post(f"/api/recruitment/{candidate['id']}/confirm"))
    other = submit(director, second)
    requests = ok(director.get("/api/recruitment?event_year=2027&view=requests"))
    assert [row["id"] for row in requests["items"]] == [other["id"]]
    assert ok(director.get("/api/recruitment/summary?event_year=2027"))["requests"] == 1
    assert ok(director.get(f"/api/recruitment/{candidate['id']}"))["stage"] == "confirmed"
    assert coverage(director, first) == 1 and coverage(director, second) == 0
    assert ok(director.get("/api/judge-directory?event_year=2027"))["items"][0]["id"] == assignment["id"]


def test_review_cannot_transfer_historical_acceptance(director):
    first, second = competition(director), competition(director, "3D Art")
    legacy = nominate(director, first, "Historical Sample")
    info = payload(first, "Historical Sample", "historical-sample@example.org")
    candidate = ok(director.post("/api/recruitment", json=info))
    assert candidate["id"] == legacy["id"]
    response = director.patch(f"/api/recruitment/{candidate['id']}", json={**info, "competition_id": second["id"]})
    assert response.status_code == 409
    saved = ok(director.get(f"/api/recruitment/{candidate['id']}"))
    assert saved["pilot_id"] == first["id"] and saved["status"] == "accepted"


@pytest.mark.parametrize("disposition", ["withdrawn", "declined"])
def test_archived_contact_stays_out_of_active_recruitment_until_next_year(director, disposition):
    first, second = competition(director), competition(director, "3D Art")
    future = pilot(director, "Next Year", 2028)
    record, other = submit(director, first), submit(director, second)
    for row in (record, other):
        review(director, row)
        respond(director, row, "accepted")
    if disposition == "withdrawn":
        assignment = ok(director.post(f"/api/recruitment/{record['id']}/confirm"))
        ok(director.post(f"/api/recruitment/assignments/{assignment['id']}/withdraw"))
    else:
        respond(director, record, "declined")
    archived = ok(director.get("/api/recruitment?event_year=2027&view=archive"))
    assert {row["id"] for row in archived["items"]} == {record["id"], other["id"]}
    assert {row["archive_status"] for row in archived["items"]} == {disposition}
    assert director.post(f"/api/recruitment/{other['id']}/confirm").status_code == 409
    assert confirm(director, second, other, 1).status_code == 409
    assert director.post(f"/api/recruitment/{other['id']}/reopen").status_code == 409
    assert director.post("/api/recruitment", json=payload(pilot(director, "Same Year"))).status_code == 409
    assert category(director, 2027)["confirmed_count"] == 0
    later = submit(director, future)
    review(director, later)
    respond(director, later, "accepted")
    ok(director.post(f"/api/recruitment/{later['id']}/confirm"))
    assert category(director, 2028)["confirmed_count"] == 1
    reopened = ok(director.post(f"/api/recruitment/{record['id']}/reopen"))
    assert reopened["review_status"] == "pending"
    retargeted = director.patch(f"/api/recruitment/{record['id']}", json=payload(second))
    assert retargeted.status_code == 409
    assert retargeted.json()["detail"]["code"] == "request_has_history"


def test_duplicate_email_cannot_hold_two_official_slots(director):
    first, second = competition(director), competition(director, "3D Art")
    original, duplicate = nominate(director, first, "Original Sample"), nominate(director, second, "Duplicate Sample")
    ok(confirm(director, first, original, 1))
    with SessionLocal.begin() as db:
        db.get(Judge, UUID(duplicate["judge_id"])).alternate_email = " ORIGINAL-SAMPLE@EXAMPLE.ORG "
    response = confirm(director, second, duplicate, 1)
    assert response.status_code == 409
    assert coverage(director, first) == 1 and coverage(director, second) == 0
    blocked = draft(director, second, "duplicate-sample@example.org")
    assert blocked.status_code == 400 and "share an email address" in blocked.text


def test_deleted_pilot_keeps_decline_until_explicit_reopen(director):
    source = pilot(director, "Archived Pilot")
    target = competition(director)
    record = submit(director, source)
    review(director, record)
    respond(director, record, "declined")
    ok(director.request("DELETE", f"/api/competitions/{source['id']}", json={
        "confirmed_first": True, "confirmed_second": True, "exact_name": source["name"],
    }))
    assert director.post("/api/recruitment", json=payload(target)).status_code == 409
    ok(director.post(f"/api/recruitment/{record['id']}/reopen"))
    assert ok(director.get(f"/api/recruitment/{record['id']}"))["archive_status"] == "inactive"
    candidate = submit(director, target)
    requests = ok(director.get("/api/recruitment?event_year=2027&view=requests"))["items"]
    assert [row["id"] for row in requests] == [candidate["id"]]


def test_contact_and_nomination_writes_detect_alternate_and_merged_addresses(director):
    first = competition(director)
    legacy = nominate(director, first, "Former Sample", accepted=False)
    survivor = ok(director.post("/api/judges", json={
        "display_name": "Survivor Sample", "primary_email": "survivor@example.org",
        "alternate_email": "alternate-survivor@example.org",
    }))
    ok(director.post(f"/api/judges/{survivor['id']}/merge", json={
        "merged_judge_id": legacy["judge_id"], "field_decisions": {}, "confirmed": True,
    }))
    duplicate = director.post(f"/api/competitions/{first['id']}/judges", json={
        "name": survivor["display_name"], "existing_judge_id": survivor["id"],
    })
    assert duplicate.status_code == 409
    candidate = submit(director, first, name="Survivor Sample", address=" Former-Sample@EXAMPLE.ORG ")
    assert candidate["id"] == legacy["id"] and candidate["judge_id"] == survivor["id"]
    with SessionLocal.begin() as db:
        db.add(Judge(display_name="Legacy Duplicate Sample", primary_email=" ALTERNATE-SURVIVOR@EXAMPLE.ORG "))
    conflict = director.post("/api/recruitment", json=payload(first, "Survivor Sample", "alternate-survivor@example.org"))
    assert conflict.status_code == 409 and conflict.json()["detail"]["code"] == "contact_conflict"


def test_technology_cannot_change_assignments_and_member_cannot_reassign(director):
    first, second = competition(director), competition(director, "3D Art")
    record, other = submit(director, first), submit(director, second)
    for row in (record, other):
        review(director, row)
        respond(director, row, "accepted")
    login(director, "tech")
    assert director.post(f"/api/recruitment/{record['id']}/confirm").status_code == 403
    login(director, "competitions")
    assignment = ok(director.post(f"/api/recruitment/{record['id']}/confirm"))
    assert confirm(director, second, other, 1, assignment["id"]).status_code == 403
    login(director, "tech")
    assert director.post(f"/api/recruitment/assignments/{assignment['id']}/withdraw").status_code == 403
    login(director, "chair")
    moved = ok(confirm(director, second, other, 1, assignment["id"]))
    assert coverage(director, first) == 0 and coverage(director, second) == 1
    assert category(director, 2027)["confirmed_count"] == 1
    assert ok(director.get("/api/judge-directory?event_year=2027"))["items"][0]["id"] == moved["id"]


def test_recruitment_and_direct_assignment_compete_for_the_last_slot(director):
    target = competition(director)
    for slot in (1, 2):
        candidate = nominate(director, target, f"Occupied Slot {slot}")
        ok(confirm(director, target, candidate, slot))
    managed = submit(director, target)
    review(director, managed)
    respond(director, managed, "accepted")
    legacy = nominate(director, target, "Other Candidate")
    cookies = dict(director.cookies)
    start = Barrier(2, timeout=10)

    def attempt(use_recruitment):
        with TestClient(app, cookies=cookies, headers={"X-MIST-Request": "1"}) as client:
            start.wait()
            if use_recruitment:
                return client.post(f"/api/recruitment/{managed['id']}/confirm").status_code
            return confirm(client, target, legacy, 3).status_code

    with ThreadPoolExecutor(max_workers=2) as pool:
        jobs = [pool.submit(attempt, via_recruitment) for via_recruitment in (True, False)]
        assert sorted(job.result(timeout=15) for job in jobs) == [200, 409]
    assert category(director, 2027)["confirmed_count"] == 3


def test_merged_contact_preserves_visible_conversation_and_assignment_history(director):
    first = competition(director)
    former = submit(director, first)
    review(director, former)
    campaign = ok(draft(director, first, "rowan@example.org"))
    queue(director, campaign)
    assert run_once("merge-history")
    respond(director, former, "accepted")
    assignment = ok(director.post(f"/api/recruitment/{former['id']}/confirm"))
    ok(director.post(f"/api/recruitment/assignments/{assignment['id']}/withdraw"))
    survivor = ok(director.post("/api/judges", json={
        "display_name": "Survivor Sample", "primary_email": "survivor@example.org", "notes": "Survivor context",
    }))
    ok(director.patch(f"/api/judges/{former['judge_id']}", json={
        "display_name": "Rowan Sample", "primary_email": "rowan@example.org",
        "notes": "Former contact context", "do_not_contact": True,
    }))
    merged = ok(director.post(f"/api/judges/{survivor['id']}/merge", json={
        "merged_judge_id": former["judge_id"], "field_decisions": {}, "confirmed": True,
    }))
    assert merged["do_not_contact"] and "Former contact context" in merged["notes"]
    preview = ok(director.get(f"/api/campaigns/{campaign['id']}/preview"))["items"]
    assert preview[0]["judge_id"] == former["judge_id"]
    reply = ok(director.post("/api/inbox/simulate", json={
        "sender_email": "rowan@example.org", "subject": "Re: Judge invitation", "body": "Sample reply.",
        "request_id": str(uuid4()), "campaign_recipient_id": preview[0]["id"],
    }))
    assert reply["judge_id"] == survivor["id"]
    thread = ok(director.get(f"/api/conversations/judges/{survivor['id']}"))
    assert [row["direction"] for row in thread["messages"]] == ["outgoing", "incoming"]
    record = ok(director.get(f"/api/recruitment/{former['id']}"))
    assert any("Released slot" in row["summary"] for row in record["history"])
    assert record["archive_status"] == "withdrawn"
    assert draft(director, first, "rowan@example.org").status_code == 400


def test_merge_refuses_same_year_archived_contact_and_live_slot(director):
    first, second = competition(director), competition(director, "3D Art")
    former = nominate(director, first, "Former Judge")
    current = nominate(director, second, "Current Judge")
    released = ok(confirm(director, first, former, 1))
    ok(director.post(f"/api/recruitment/assignments/{released['id']}/withdraw"))
    active = ok(confirm(director, second, current, 1))
    response = director.post(f"/api/judges/{current['judge_id']}/merge", json={
        "merged_judge_id": former["judge_id"], "field_decisions": {}, "confirmed": True,
    })
    assert response.status_code == 409
    assert ok(director.get(f"/api/judges/{former['judge_id']}"))["merged_into_id"] is None
    assert ok(director.get("/api/judge-directory?event_year=2027"))["items"][0]["id"] == active["id"]
    assert ok(director.get(f"/api/recruitment/{former['id']}"))["archive_status"] == "withdrawn"


@pytest.mark.parametrize("archive_survives", [False, True])
def test_merge_cannot_archive_an_active_request_as_a_side_effect(director, archive_survives):
    first, second = competition(director), competition(director, "3D Art")
    former = submit(director, first, name="Former Judge", address="former@example.org")
    current = submit(director, second, name="Current Judge", address="current@example.org")
    review(director, former)
    respond(director, former, "declined")
    active_id, archived_id = current["judge_id"], former["judge_id"]
    survivor, merged = (archived_id, active_id) if archive_survives else (active_id, archived_id)
    response = director.post(f"/api/judges/{survivor}/merge", json={
        "merged_judge_id": merged, "field_decisions": {}, "confirmed": True,
    })
    assert response.status_code == 409
    assert response.json()["detail"]["code"] == "request_merge_conflict"
    assert ok(director.get(f"/api/judges/{merged}"))["merged_into_id"] is None
    assert ok(director.get(f"/api/recruitment/{current['id']}"))["archive_status"] == ""


def test_merged_legacy_decline_can_be_reopened_without_overwriting_canonical_request(director):
    first = competition(director)
    former = nominate(director, first, "Former Contact")
    current = nominate(director, first, "Current Contact", accepted=False)
    ok(director.patch(f"/api/competitions/{first['id']}/judges/{former['id']}", json={"status": "declined"}))
    ok(director.post(f"/api/judges/{current['judge_id']}/merge", json={
        "merged_judge_id": former["judge_id"], "field_decisions": {}, "confirmed": True,
    }))
    historical = ok(director.get(f"/api/recruitment/{former['id']}"))
    assert historical["archive_status"] == "declined" and historical["can_reopen_archive"]
    reopened = ok(director.post(f"/api/recruitment/{former['id']}/reopen"))
    assert reopened["judge_id"] == former["judge_id"]
    rows = ok(director.get("/api/recruitment?event_year=2027"))["items"]
    assert [row["id"] for row in rows] == [current["id"]]


def test_year_scoped_outreach_does_not_update_legacy_other_year(director):
    first, later = competition(director), pilot(director, "Later Year", 2028)
    old = nominate(director, first, "Year Scope", accepted=False)
    new = nominate(director, later, "Year Scope", judge_id=old["judge_id"], accepted=False)
    campaign = ok(draft(director, None, "year-scope@example.org", event_year=2028))
    queue(director, campaign)
    assert run_once("year-scope")
    with SessionLocal() as db:
        assert db.get(PilotNomination, UUID(old["id"])).status == "to_contact"
        assert db.get(PilotNomination, UUID(new["id"])).status == "awaiting_response"
    recipient = ok(director.get(f"/api/campaigns/{campaign['id']}/preview"))["items"][0]
    ok(director.post("/api/inbox/simulate", json={
        "sender_email": "year-scope@example.org", "subject": "Re: Judge invitation", "body": "Sample reply.",
        "request_id": str(uuid4()), "campaign_recipient_id": recipient["id"],
    }))
    assert category(director, 2027)["unread_count"] == 0
    assert category(director, 2028)["unread_count"] == 1
