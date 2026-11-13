import os
from concurrent.futures import ThreadPoolExecutor
from threading import Barrier
from uuid import UUID

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select

from mist.db import SessionLocal
from mist.main import app
from mist.models import Audit, Judge, OfficialJudgeAssignment, PilotCompetition, PilotNomination


def ok(response):
    assert response.status_code == 200, response.text
    return response.json()


def pilot(client, name, year=2027, category="arts"):
    return ok(client.post("/api/competitions", data={
        "name": name, "event_year": str(year), "category_slug": category,
    }))


def nominate(client, competition, name, judge_id=None, accepted=True):
    # A pre-Phase 3 nomination has no review state. Keep coverage for that data path.
    with SessionLocal() as db:
        judge = db.get(Judge, UUID(judge_id)) if judge_id else Judge(
            display_name=name, primary_email=f"{name.lower().replace(' ', '-')}@example.org",
        )
        db.add(judge)
        db.flush()
        nomination = PilotNomination(pilot_id=UUID(competition["id"]), judge_id=judge.id,
                                     status="accepted" if accepted else "to_contact")
        db.add(nomination)
        db.commit()
        return {"id": str(nomination.id), "judge_id": str(judge.id)}


def confirm(client, competition, nomination, slot, previous=None):
    return client.post(f"/api/competitions/{competition['id']}/assignments", json={
        "nomination_id": nomination["id"], "slot_number": slot,
        "previous_assignment_id": previous,
    })


def release(client, competition, assignment):
    return client.delete(f"/api/competitions/{competition['id']}/assignments/{assignment['id']}")


def detail(client, competition):
    return ok(client.get(f"/api/competitions/{competition['id']}"))


def category(client, year, slug="arts"):
    return next(row for row in ok(client.get(f"/api/competitions/categories?event_year={year}")) if row["slug"] == slug)


def history(client, competition):
    return ok(client.get(f"/api/competitions/{competition['id']}/assignments/history"))["items"]


def login(client, account):
    return ok(client.post("/api/auth/login", json={"email": f"{account}@example.com", "password": os.environ["SEED_PASSWORD"]}))


def test_accepted_recruitment_does_not_fill_slots_and_coverage_is_year_scoped(director):
    current = pilot(director, "Annual Showcase")
    next_year = pilot(director, "Annual Showcase", 2028)
    judge = nominate(director, current, "Example Judge")
    later = nominate(director, next_year, "Example Judge", judge["judge_id"])
    assert detail(director, current)["counts"] == {"accepted": 1}
    assert detail(director, current)["official_assignments"] == []
    assert category(director, 2027)["required_count"] == 24
    assert category(director, 2027)["confirmed_count"] == 0
    assert ok(director.get("/api/competitions/tournaments")) == {"years": [2028, 2027], "default_year": 2028}
    ok(confirm(director, current, judge, 1))
    assert category(director, 2027)["confirmed_count"] == 1
    assert category(director, 2028)["confirmed_count"] == 0
    assert category(director, 2028)["required_count"] == 3
    ok(confirm(director, next_year, later, 1))
    assert category(director, 2027)["confirmed_count"] == category(director, 2028)["confirmed_count"] == 1
    scoped = ok(director.get("/api/competitions?event_year=2028"))["items"]
    assert [row["id"] for row in scoped] == [next_year["id"]]
    assert scoped[0]["confirmed_count"] == 1


def test_slots_cap_at_three_and_release_retains_recruitment_and_history(director):
    competition = pilot(director, "Capacity")
    judge = nominate(director, competition, "Sample One", accepted=False)
    response = confirm(director, competition, judge, 1)
    assert response.status_code == 409 and response.json()["detail"]["code"] == "acceptance_required"
    ok(director.patch(f"/api/competitions/{competition['id']}/judges/{judge['id']}", json={"status": "accepted"}))
    judges = [judge] + [nominate(director, competition, f"Sample {number}") for number in (2, 3, 4)]
    confirmed = []
    for slot in (1, 2, 3):
        confirmed.append(ok(confirm(director, competition, judges[slot - 1], slot)))
        state = detail(director, competition)
        assert (state["confirmed_count"], state["required_count"]) == (slot, 3)
    assert confirm(director, competition, judges[3], 4).status_code == 422
    for slot in (1, 2, 3):
        response = confirm(director, competition, judges[3], slot)
        assert response.status_code == 409 and response.json()["detail"]["code"] == "slot_occupied"
    assert category(director, 2027)["confirmed_count"] == 3
    ok(release(director, competition, confirmed[1]))
    assert detail(director, competition)["confirmed_count"] == 2
    assert detail(director, competition)["counts"] == {"accepted": 4}
    old = next(item for item in history(director, competition) if item["id"] == confirmed[1]["id"])
    assert old["released_at"] and old["released_by"] and old["release_reason"] == "released"
    ok(confirm(director, competition, judges[3], 2))
    assert detail(director, competition)["confirmed_count"] == 3


def test_reassignment_requires_explicit_source_and_leaves_source_on_failure(director):
    first, second = pilot(director, "Source"), pilot(director, "Destination")
    judge = nominate(director, first, "Moving Judge")
    target = nominate(director, second, "Moving Judge", judge["judge_id"])
    original = ok(confirm(director, first, judge, 1))
    blocker = nominate(director, second, "Destination Judge")
    ok(confirm(director, second, blocker, 1))
    duplicate = confirm(director, second, target, 2)
    assert duplicate.status_code == 409 and duplicate.json()["detail"]["code"] == "judge_already_assigned"
    assert confirm(director, second, target, 1, original["id"]).status_code == 409
    assert detail(director, first)["official_assignments"][0]["id"] == original["id"]
    moved = ok(confirm(director, second, target, 2, original["id"]))
    assert detail(director, first)["official_assignments"] == []
    assert detail(director, first)["counts"] == {"accepted": 1}
    assert moved["judge_id"] == judge["judge_id"]
    assert history(director, first)[0]["release_reason"] == "reassigned"
    stale = confirm(director, first, judge, 1, original["id"])
    assert stale.status_code == 409 and stale.json()["detail"]["code"] == "assignment_changed"
    with SessionLocal() as db:
        audit = db.scalar(select(Audit).where(Audit.action == "judge_assignment.reassigned"))
        assert audit.after_data["previous_assignment_id"] == original["id"]


def test_pending_and_inactive_pilots_are_excluded_from_active_coverage(director):
    pending = pilot(director, "Awaiting review")
    inactive = pilot(director, "Inactive pilot")
    judge = nominate(director, inactive, "Inactive Judge")
    ok(confirm(director, inactive, judge, 1))
    candidate = nominate(director, pending, "Pending Judge")
    with SessionLocal() as db:
        db.get(PilotCompetition, UUID(pending["id"])).activation_status = "pending_approval"
        db.get(PilotCompetition, UUID(inactive["id"])).activation_status = "inactive"
        db.commit()
    arts = category(director, 2027)
    assert (arts["competition_count"], arts["required_count"], arts["confirmed_count"]) == (7, 21, 0)
    visible = ok(director.get("/api/competitions?category_slug=arts&event_year=2027"))["items"]
    assert {pending["id"], inactive["id"]}.isdisjoint(row["id"] for row in visible)
    assert detail(director, inactive)["official_assignments"]
    assert detail(director, inactive)["required_count"] == 0
    response = confirm(director, pending, candidate, 1)
    assert response.status_code == 409 and response.json()["detail"]["code"] == "competition_inactive"


@pytest.mark.parametrize("same_judge", [False, True])
def test_concurrent_requests_cannot_double_book_a_slot_or_judge(director, same_judge):
    first = pilot(director, "Race A")
    first_judge = nominate(director, first, "Race First")
    if same_judge:
        second = pilot(director, "Race B")
        second_judge = nominate(director, second, "Race First", first_judge["judge_id"])
    else:
        second = first
        second_judge = nominate(director, second, "Race Second")
    barrier = Barrier(2)
    cookies = dict(director.cookies)

    def attempt(competition, judge):
        with TestClient(app, cookies=cookies, headers={"X-MIST-Request": "1"}) as client:
            barrier.wait(timeout=10)
            return confirm(client, competition, judge, 1).status_code

    with ThreadPoolExecutor(max_workers=2) as pool:
        results = [pool.submit(attempt, first, first_judge), pool.submit(attempt, second, second_judge)]
        assert sorted(result.result(timeout=15) for result in results) == [200, 409]
    assert category(director, 2027)["confirmed_count"] == 1


def test_contact_merge_conflicts_roll_back_and_safe_merge_preserves_slot(director):
    first, second = pilot(director, "Merge A"), pilot(director, "Merge B")
    survivor = nominate(director, first, "Survivor Contact")
    duplicate = nominate(director, second, "Duplicate Contact")
    survivor_assignment = ok(confirm(director, first, survivor, 1))
    retained = ok(confirm(director, second, duplicate, 2))
    url = f"/api/judges/{survivor['judge_id']}/merge"
    payload = {"merged_judge_id": duplicate["judge_id"], "field_decisions": {}, "confirmed": True}
    response = director.post(url, json=payload)
    assert response.status_code == 409 and response.json()["detail"]["code"] == "assignment_merge_conflict"
    with SessionLocal() as db:
        assert db.get(Judge, UUID(duplicate["judge_id"])).merged_into_id is None
    assert category(director, 2027)["confirmed_count"] == 2
    ok(release(director, first, survivor_assignment))
    ok(director.post(url, json=payload))
    moved = detail(director, second)["official_assignments"][0]
    assert moved["id"] == retained["id"] and moved["judge_id"] == survivor["judge_id"]
    assert moved["judge_name"] == "Survivor Contact"
    with SessionLocal() as db:
        assert db.get(PilotNomination, UUID(duplicate["id"])).judge_id == UUID(duplicate["judge_id"])
        assert db.scalar(select(Audit).where(Audit.action == "judge_assignment.contact_merged"))
        assert db.get(OfficialJudgeAssignment, UUID(retained["id"])).judge_name == "Duplicate Contact"


def test_chair_and_director_manage_slots_members_view_and_recruit(director):
    competition = pilot(director, "Permissions")
    login(director, "competitions")
    candidate = nominate(director, competition, "Sample Member")
    assert confirm(director, competition, candidate, 1).status_code == 403
    login(director, "chair")
    assignment = ok(confirm(director, competition, candidate, 1))
    login(director, "competitions")
    assert detail(director, competition)["confirmed_count"] == 1
    assert release(director, competition, assignment).status_code == 403
    login(director, "chair")
    ok(release(director, competition, assignment))


def test_pilot_deletion_and_explicit_directory_clear_preserve_slot_history(director):
    deleted, remaining = pilot(director, "Pilot to delete"), pilot(director, "Pilot to keep")
    first = nominate(director, deleted, "Pilot Judge")
    second = nominate(director, remaining, "Roster Judge")
    ok(confirm(director, deleted, first, 1))
    ok(confirm(director, remaining, second, 2))
    ok(director.request("DELETE", f"/api/competitions/{deleted['id']}", json={
        "confirmed_first": True, "confirmed_second": True, "exact_name": deleted["name"],
    }))
    assert history(director, deleted)[0]["release_reason"] == "pilot_deleted"
    backup = ok(director.post("/api/judge-directory/clear/prepare"))
    ok(director.post("/api/judge-directory/clear", json={
        "confirmed_first": True, "confirmed_second": True, "backup_id": backup["id"],
        "password": os.environ["SEED_PASSWORD"],
    }))
    assert detail(director, remaining)["confirmed_count"] == 0
    archived = history(director, remaining)[0]
    assert archived["release_reason"] == "directory_cleared"
    assert archived["judge_id"] is None and archived["judge_name"] == "Roster Judge"
