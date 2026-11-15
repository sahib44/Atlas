from uuid import uuid4

from mist.worker import run_once
from test_directory_email import campaign_outbox, ok
from test_workflows import queue


def test_campaign_creation_replay_keeps_one_campaign_and_new_id_can_resend(director):
    payload = {
        "request_id": str(uuid4()),
        "name": "Replay example", "subject_template": "Sample invitation", "body_template": "Hello",
        "recipients": [{"email": "campaign-replay@example.org"}],
    }
    response = director.post("/api/campaigns", json=payload)
    first = ok(response)
    repeated = ok(director.post("/api/campaigns", json=payload))
    assert repeated["id"] == first["id"]
    assert repeated["recipient_count"] == first["recipient_count"] == 1
    assert repeated["excluded"] == first["excluded"]
    assert director.post("/api/campaigns", json={**payload, "body_template": "Different"}).status_code == 409
    queue(director, first)
    ok(director.post(f"/api/campaigns/{first['id']}/queue"))
    assert run_once("campaign-replay")
    assert not run_once("campaign-replay")
    assert [row["status"] for row in campaign_outbox(director, first)] == ["sent"]

    another = ok(director.post("/api/campaigns", json={**payload, "request_id": str(uuid4())}))
    assert another["id"] != first["id"]
    queue(director, another)
    assert run_once("campaign-replay")
    assert [row["status"] for row in campaign_outbox(director, another)] == ["sent"]


def test_worker_cancels_raw_recipient_when_the_competition_is_deleted(director):
    competition = ok(director.post("/api/competitions", data={
        "name": "Sample Pilot", "event_year": 2027, "category_slug": "arts",
    }))
    campaign = ok(director.post("/api/campaigns", json={
        "name": "Sample campaign", "subject_template": "Sample invitation", "body_template": "Hello",
        "recipients": [{"email": "inactive-competition@example.org"}],
        "pilot_competition_id": competition["id"],
    }))
    queue(director, campaign)
    ok(director.request("DELETE", f"/api/competitions/{competition['id']}", json={
        "confirmed_first": True, "confirmed_second": True, "exact_name": competition["name"],
    }))
    assert run_once("competition-deleted")
    (message,) = campaign_outbox(director, campaign)
    assert message["status"] == "cancelled"
    assert message["last_error"] == "Competition is inactive"
    assert message["provider_message_id"] is None
