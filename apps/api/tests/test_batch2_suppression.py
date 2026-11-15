import os

from mist.worker import run_once
from test_directory_email import campaign_outbox, create_campaign, create_judge, ok
from test_workflows import queue


def test_directory_clear_retains_address_suppression_and_outbox_history(director):
    address = "retained-opt-out@example.org"
    judge = create_judge(
        director, "Sample Opt Out", "retained-primary@example.org", alternate_email=address,
    )
    queued = ok(create_campaign(director, [{"email": address}]))
    queue(director, queued)
    ok(director.patch(f"/api/judges/{judge['id']}", json={
        "display_name": judge["display_name"], "primary_email": judge["primary_email"],
        "alternate_email": address, "do_not_contact": True,
    }))
    backup = ok(director.post("/api/judge-directory/clear/prepare"))
    ok(director.post("/api/judge-directory/clear", json={
        "confirmed_first": True, "confirmed_second": True,
        "password": os.environ["SEED_PASSWORD"], "backup_id": backup["id"],
    }))
    assert ok(director.get("/api/judges"))["total"] == 0
    assert run_once("clear-dnc-regression")
    (message,) = campaign_outbox(director, queued)
    assert message["status"] == "cancelled"
    assert message["last_error"] == "Do not contact"
    assert message["attempt_count"] == 0
    assert message["provider_message_id"] is None
    assert director.get(backup["download_url"]).status_code == 200

    for tournament in ({}, {"event_year": 2027}, {"event_year": 2028}):
        response = director.post("/api/campaigns", json={
            "name": "Sample outreach", "subject_template": "Invitation", "body_template": "Hello",
            "recipients": [{"email": " Retained-Opt-Out@EXAMPLE.ORG "}], **tournament,
        })
        assert response.status_code == 400
        assert "Do not contact" in response.json()["detail"]["message"]

    replacement = create_judge(director, "Restored Sample", address)
    assert create_campaign(director, judge_ids=[replacement["id"]]).status_code == 400
    ok(director.patch(f"/api/judges/{replacement['id']}", json={
        "display_name": replacement["display_name"], "primary_email": address, "do_not_contact": False,
    }))
    fresh = ok(create_campaign(director, [{"email": address}]))
    queue(director, fresh)
    assert run_once("clear-dnc-regression")
    assert campaign_outbox(director, fresh)[0]["status"] == "sent"
    assert campaign_outbox(director, queued)[0]["status"] == "cancelled"
