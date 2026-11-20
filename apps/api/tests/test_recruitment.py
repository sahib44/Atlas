import csv
import io
import os

from fastapi.testclient import TestClient

from mist.main import app
from mist.worker import run_once
from test_workflows import queue as queue_campaign


def ok(response):
    assert response.status_code == 200, response.text
    return response.json()


def login(client, role):
    ok(client.post("/api/auth/login", json={"email": f"{role}@example.com", "password": os.environ["SEED_PASSWORD"]}))


def competition(client, name="2D Art"):
    return next(row for row in ok(client.get("/api/competitions?event_year=2027&page_size=100"))["items"] if row["name"] == name)


def payload(target, name="Rowan Sample", address="rowan@example.org"):
    return {"competition_id": target["id"], "name": name, "email": address, "phone": "555-0100",
            "qualifications": "Led regional art reviews for three seasons.",
            "notes": "Available in the afternoon.", "prior_involvement": "Former MIST participant"}


def submit(client, target, **kwargs):
    return ok(client.post("/api/recruitment", json=payload(target, **kwargs)))


def review(client, record, decision="approved"):
    return ok(client.post(f"/api/recruitment/{record['id']}/review", json={
        "decision": decision, "version": record["updated_at"],
    }))


def respond(client, record, status):
    return ok(client.post(f"/api/recruitment/{record['id']}/response", json={"status": status}))


def draft(client, target, address, **extra):
    return client.post("/api/campaigns", json={
        "name": "Example invitation", "subject_template": "Judge invitation", "body_template": "Hello {{judge_name}}",
        "pilot_competition_id": target["id"] if target else None, "recipients": [{"email": address}], **extra,
    })


def coverage(client, target):
    return ok(client.get(f"/api/competitions/{target['id']}"))["confirmed_count"]


def test_chair_reviews_current_request_and_authorizes_sandbox_outreach(director):
    first, second = competition(director), competition(director, "3D Art")
    with TestClient(app, headers={"X-MIST-Request": "1"}) as member:
        login(member, "competitions")
        record = submit(member, first)
        path = f"/api/recruitment/{record['id']}"
        assert record["review_status"] == "pending"
        assert ok(member.get(path))["returning"]
        assert ok(member.get("/api/recruitment?event_year=2027&view=requests"))["total"] == 1
        assert ok(member.get("/api/recruitment/summary?event_year=2027"))["requests"] == 1
        assert not ok(member.get("/api/notifications"))["items"]
        assert member.post(path + "/review", json={"decision": "approved", "version": record["updated_at"]}).status_code == 403
        assert member.post(path + "/response", json={"status": "accepted"}).status_code == 409
        assert member.post(path + "/confirm").status_code == 409
        assert "Chair approval" in draft(member, first, "rowan@example.org").text
        assert "Chair approval" in draft(member, None, "rowan@example.org").text

        login(director, "chair")
        notification = ok(director.get("/api/notifications"))["items"][0]
        assert notification["kind"] == "judge_request" and f"candidate={record['id']}" in notification["href"]
        edited = ok(director.patch(path, json={**payload(second), "notes": "Chair reviewed the updated category."}))
        assert edited["pilot_id"] == second["id"]
        assert director.post(path + "/review", json={"decision": "approved", "version": record["updated_at"]}).status_code == 409
        review(director, edited)
        assert any(item["kind"] == "judge_reviewed" for item in ok(member.get("/api/notifications"))["items"])
        assert "No approved request" in draft(member, first, "rowan@example.org").text
        campaign = ok(draft(member, second, "rowan@example.org"))
        queue_campaign(member, campaign)
        assert run_once("recruitment-test")
        latest = ok(member.get(path))
        assert latest["last_contacted_at"] and latest["status"] == "awaiting_response"
        assert ok(member.get("/api/recruitment/summary?event_year=2027"))["contacted"] == 1
        assert ok(member.get("/api/recruitment?event_year=2027&stage=contacted"))["total"] == 1
        assert ok(member.get(f"/api/conversations/judges/{record['judge_id']}"))["messages"][0]["status"] == "sent"
        respond(member, record, "accepted")
        assert coverage(member, second) == 0
        assigned = ok(member.post(path + "/confirm"))
        assert assigned["competition_id"] == second["id"] and assigned["slot_number"] == 1
        assert coverage(member, second) == 1
        arts = next(row for row in ok(member.get("/api/competitions/categories?event_year=2027")) if row["slug"] == "arts")
        assert arts["confirmed_count"] == 1
        assert ok(member.get("/api/recruitment?event_year=2027"))["total"] == 0
        confirmed = ok(member.get("/api/judge-directory?event_year=2027"))["items"]
        assert confirmed[0]["notes"] == "Chair reviewed the updated category."
        sheet = member.get("/api/judge-directory/export?event_year=2027")
        assert list(csv.DictReader(io.StringIO(sheet.content.decode("utf-8-sig"))))[0]["Judge"] == "Rowan Sample"
        assert ok(member.get("/api/judge-directory?event_year=2028"))["total"] == 0
        assert ok(member.get("/api/recruitment?event_year=2028"))["total"] == 0
        ok(member.post(f"/api/recruitment/assignments/{assigned['id']}/withdraw"))
        assert coverage(member, second) == 0
        assert ok(member.get("/api/judge-directory?event_year=2027"))["total"] == 0
        assert ok(member.get("/api/recruitment?event_year=2027&view=archive"))["items"][0]["archive_status"] == "withdrawn"
        assert ok(member.get(f"/api/judge-directory/assignments/{assigned['id']}/history"))["items"][0]["release_reason"] == "withdrawn"
        assert "withdrew" in draft(member, second, "rowan@example.org").text
        assert member.post(path + "/confirm").status_code == 409
        reopened = ok(member.post(path + "/reopen"))
        assert reopened["review_status"] == "pending" and reopened["status"] == "to_contact"
        assert ok(member.get("/api/recruitment?event_year=2027&view=archive"))["total"] == 0
        assert "Chair approval" in draft(member, second, "rowan@example.org").text
        review(director, reopened)
        assert ok(member.get(path))["stage"] == "approved"
        ok(draft(member, second, "rowan@example.org"))
        respond(member, reopened, "accepted")
        reassigned = ok(member.post(path + "/confirm"))
        assert reassigned["id"] != assigned["id"]
        history = ok(member.get(f"/api/judge-directory/assignments/{reassigned['id']}/history"))["items"]
        assert {row["release_reason"] for row in history} == {"withdrawn", None}


def test_rejection_decline_and_explicit_followup(director):
    target = competition(director)
    rejected = submit(director, target, name="Mira Example", address="mira@example.org")
    review(director, rejected, "rejected")
    assert "Chair approval" in draft(director, target, "mira@example.org").text
    declined = submit(director, target)
    review(director, declined)
    campaign = ok(draft(director, target, "rowan@example.org"))
    queue_campaign(director, campaign)
    respond(director, declined, "declined")
    assert run_once("cancel-stale-outreach")
    outbox = ok(director.get(f"/api/outbox?campaign_id={campaign['id']}"))["items"]
    assert outbox[0]["status"] == "cancelled"
    assert "declined" in draft(director, target, "rowan@example.org").text.lower()
    assert draft(director, None, "rowan@example.org", allow_declined_outreach=True).status_code == 400
    ok(draft(director, target, "rowan@example.org", allow_declined_outreach=True))
    assert ok(director.get("/api/recruitment?event_year=2027"))["total"] == 0
    assert {row["archive_status"] for row in ok(director.get("/api/recruitment?event_year=2027&view=archive"))["items"]} == {"declined", "rejected"}


def test_recruitment_respects_capacity_one_assignment_and_contact_reuse(director):
    target, other = competition(director), competition(director, "3D Art")
    records = []
    for index in range(4):
        record = submit(director, target, name=f"Sample Judge {index}", address=f"sample-{index}@example.org")
        review(director, record)
        respond(director, record, "accepted")
        records.append(record)
    for record in records[:3]:
        ok(director.post(f"/api/recruitment/{record['id']}/confirm"))
    assert "three confirmed" in director.post(f"/api/recruitment/{records[-1]['id']}/confirm").text
    assert coverage(director, target) == 3
    assert ok(director.get("/api/judge-directory?event_year=2027"))["total"] == 3
    sheet = director.get("/api/judge-directory/export?event_year=2027")
    assert "Sample Judge 3" not in sheet.text
    duplicate = submit(director, other, name="Sample Judge 0", address="sample-0@example.org")
    assert duplicate["judge_id"] == records[0]["judge_id"]
    review(director, duplicate)
    respond(director, duplicate, "accepted")
    assert "judge_already_assigned" in director.post(f"/api/recruitment/{duplicate['id']}/confirm").text
    assert director.post("/api/recruitment", json=payload(other, "Sample Judge 0", "sample-0@example.org")).status_code == 409


def test_existing_competition_entry_point_cannot_bypass_review(director):
    target = competition(director)
    record = ok(director.post(f"/api/competitions/{target['id']}/judges", json={
        "name": "Alternate Entry", "email": "alternate@example.org", "qualifications": "Experienced adjudicator",
    }))
    assert record["review_status"] == "pending"
    assert "Chair approval" in draft(director, target, "alternate@example.org").text
    assert director.patch(f"/api/competitions/{target['id']}/judges/{record['id']}", json={"status": "accepted"}).status_code == 409
