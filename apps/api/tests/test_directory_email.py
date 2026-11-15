import csv
import io
from uuid import UUID

import pytest

from mist.db import SessionLocal
from mist.models import Judge, Outbound
from mist.worker import claim, deliver, run_once
from test_recruitment import review
from test_workflows import queue


def ok(response):
    assert response.status_code == 200, response.text
    return response.json()


def create_judge(client, name, address, **extra):
    return ok(client.post("/api/judges", json={
        "display_name": name, "primary_email": address, **extra,
    }))


def set_do_not_contact(client, judge, suppressed):
    return ok(client.patch(f"/api/judges/{judge['id']}", json={
        "display_name": judge["display_name"],
        "primary_email": judge["primary_email"],
        "alternate_email": judge["alternate_email"],
        "do_not_contact": suppressed,
    }))


def merge_contacts(client, survivor, former):
    return ok(client.post(f"/api/judges/{survivor['id']}/merge", json={
        "merged_judge_id": former["id"], "field_decisions": {}, "confirmed": True,
    }))


def create_campaign(client, recipients=(), judge_ids=()):
    return client.post("/api/campaigns", json={
        "name": "Sample invitations", "subject_template": "Invitation",
        "body_template": "Hello {{judge_name}}", "recipients": recipients,
        "judge_ids": judge_ids,
    })


def campaign_outbox(client, campaign):
    return ok(client.get("/api/outbox", params={"campaign_id": campaign["id"]}))["items"]


def assert_dnc_cancellation(client, message):
    assert message["status"] == "cancelled"
    assert "do not contact" in message["last_error"].lower()
    assert message["attempt_count"] == 0
    assert message["provider_message_id"] is None
    events = ok(client.get("/api/audit-events", params={"q": "message.delivery_cancelled"}))["items"]
    event = next(event for event in events if event["entity_id"] == message["id"])
    assert event["entity_type"] == "outbound_messages"
    assert event["after_data"]["status"] == "cancelled"
    assert "do not contact" in event["after_data"]["reason"].lower()


def test_directory_tracks_competition_response_qualification_and_sheet_order(director):
    arts = ok(director.get("/api/competitions?category_slug=arts&page_size=100"))["items"]
    competition = next(row for row in arts if row["name"] == "2D Art")
    first = ok(director.post(f"/api/competitions/{competition['id']}/judges", json={
        "name": "First Sample", "email": "first@example.org", "qualifications": "Has led youth art workshops for five years.",
    }))
    second = ok(director.post(f"/api/competitions/{competition['id']}/judges", json={
        "name": "Second Sample", "phone": "555-0122", "qualifications": "Experienced local artist.",
    }))
    review(director, first)
    ok(director.patch(f"/api/competitions/{competition['id']}/judges/{first['id']}", json={"status": "accepted"}))
    assert ok(director.get("/api/judge-directory?event_year=2027"))["total"] == 0
    backup = ok(director.post("/api/judge-directory/clear/prepare"))
    response = director.get(backup["download_url"])
    assert response.status_code == 200
    sheet = [row for row in csv.DictReader(io.StringIO(response.content.decode("utf-8-sig")))
             if row["Competition"] == "2D Art"]
    assert [row["Name"] for row in sheet] == ["First Sample", "Second Sample"]
    assert sheet[0]["Main category"] == "Arts"
    assert sheet[0]["Competition"] == "2D Art"
    assert sheet[0]["Response"] == "accepted"
    assert sheet[0]["Qualification"] == "Has led youth art workshops for five years."
    assert second["id"] != first["id"]


def test_campaign_attachment_and_sandbox_forward(director):
    arts = ok(director.get("/api/competitions?category_slug=arts&page_size=100"))["items"]
    competition = next(row for row in arts if row["name"] == "3D Art")
    record = ok(director.post(f"/api/competitions/{competition['id']}/judges", json={
        "name": "Attachment Sample", "email": "attachment@example.org", "qualifications": "Art instructor",
    }))
    review(director, record)
    judge = ok(director.get(f"/api/competitions/{competition['id']}/judges"))["items"][0]["judge"]
    campaign = ok(director.post("/api/campaigns", json={
        "name": "Art invite", "subject_template": "Invitation", "body_template": "Hello {{judge_name}}",
        "judge_ids": [judge["id"]], "pilot_competition_id": competition["id"],
    }))
    url = f"/api/campaigns/{campaign['id']}/attachments"
    assert director.post(url, files={"file": ("run.exe", b"MZbad", "application/octet-stream")}).status_code == 400
    attachment = ok(director.post(url, files={"file": ("rules.pdf", b"%PDF-1.4\n%mock", "application/pdf")}))
    assert [a["filename"] for a in ok(director.get(f"/api/campaigns/{campaign['id']}"))["attachments"]] == ["rules.pdf"]
    assert director.get(f"{url}/{attachment['id']}").content.startswith(b"%PDF-")
    for transition in ("submit", "approve", "queue"):
        ok(director.post(f"/api/campaigns/{campaign['id']}/{transition}"))
    assert director.post(url, files={"file": ("later.txt", b"too late", "text/plain")}).status_code == 400
    run_once("attachment-test")
    original = ok(director.get(f"/api/outbox?campaign_id={campaign['id']}"))["items"][0]
    assert original["status"] == "sent"
    assert original["attachments"][0]["filename"] == "rules.pdf"
    forwarded = ok(director.post(f"/api/outbox/{original['id']}/forward", json={
        "recipient_email": "registration@example.com", "note": "Please review.",
    }))
    assert forwarded["forwarded_from_id"] == original["id"]
    assert forwarded["attachments"][0]["id"] == attachment["id"]
    assert director.get(f"/api/outbox/{forwarded['id']}/attachments/{attachment['id']}").content.startswith(b"%PDF-")
    run_once("forward-test")
    sent = ok(director.get("/api/outbox?q=registration%40example.com"))["items"][0]
    assert sent["status"] == "sent"
    assert "Please review." in sent["body_text"]


def test_campaign_dnc_excludes_linked_contacts_and_normalized_raw_aliases(director):
    survivor = create_judge(
        director, "Suppressed Judge", "suppressed-primary@example.org",
        alternate_email="suppressed-alternate@example.org", do_not_contact=True,
    )
    former = create_judge(director, "Former Judge", "suppressed-merged@example.org")
    survivor = merge_contacts(director, survivor, former)
    assert survivor["preserved_emails"] == [former["primary_email"]]
    with SessionLocal() as db:
        # Simulate legacy contact data that predates write-time normalization.
        row = db.get(Judge, UUID(survivor["id"]))
        row.primary_email = " SUPPRESSED-PRIMARY@Example.org\t"
        row.alternate_email = "\tSUPPRESSED-ALTERNATE@Example.org "
        row.preserved_emails = ["\xa0SUPPRESSED-MERGED@Example.org\u202f"]
        db.commit()

    blocked = [
        {"name": "Raw primary", "email": " Suppressed-Primary@EXAMPLE.ORG "},
        {"name": "Raw alternate", "email": " Suppressed-Alternate@EXAMPLE.ORG ", "add_to_directory": True},
        {"name": "Raw merged alias", "email": " Suppressed-Merged@EXAMPLE.ORG "},
    ]
    allowed = {"email": "invited@example.org"}
    directory_count = ok(director.get("/api/judges"))["total"]
    campaign = ok(create_campaign(director, [*blocked, allowed], [survivor["id"]]))
    assert campaign["recipient_count"] == 1
    assert {entry["judge_name"]: entry["reason"] for entry in campaign["excluded"]} == {
        "Suppressed Judge": "Do not contact",
        "Raw primary": "Do not contact",
        "Raw alternate": "Do not contact",
        "Raw merged alias": "Do not contact",
    }
    assert ok(director.get("/api/judges"))["total"] == directory_count
    queue(director, campaign)
    assert run_once("campaign-alias-dnc-test")
    (message,) = campaign_outbox(director, campaign)
    assert message["recipient_email"] == allowed["email"]
    assert message["status"] == "sent"

    response = create_campaign(director, blocked)
    assert response.status_code == 400, response.text
    assert response.json()["detail"]["code"] == "no_recipients"


@pytest.mark.parametrize("directory_entry_after_queue", [False, True], ids=["before-queue", "after-queue"])
def test_campaign_suppresses_raw_recipient_added_to_directory_after_draft(director, directory_entry_after_queue):
    raw_recipient = {"email": "late-directory@example.org"}
    campaign = ok(create_campaign(director, [raw_recipient]))
    preview_url = f"/api/campaigns/{campaign['id']}/preview"
    (recipient,) = ok(director.get(preview_url))["items"]
    assert recipient["judge_id"] is None
    if directory_entry_after_queue:
        queue(director, campaign)
    judge = create_judge(director, "Late Directory Judge", raw_recipient["email"], do_not_contact=True)

    if directory_entry_after_queue:
        assert run_once("campaign-late-directory-test")
    else:
        queue(director, campaign)
    (message,) = campaign_outbox(director, campaign)
    assert_dnc_cancellation(director, message)
    (recipient,) = ok(director.get(preview_url))["items"]
    assert recipient["status"] == "cancelled"
    assert "do not contact" in recipient["failure_reason"].lower()

    set_do_not_contact(director, judge, False)
    ok(director.post(f"/api/campaigns/{campaign['id']}/queue"))
    assert [message["status"] for message in campaign_outbox(director, campaign)] == ["cancelled"]
    fresh = ok(create_campaign(director, [raw_recipient]))
    queue(director, fresh)
    assert run_once("campaign-queue-dnc-test")
    (message,) = campaign_outbox(director, fresh)
    assert message["recipient_email"] == raw_recipient["email"]
    assert message["status"] == "sent"


def test_worker_rechecks_actual_campaign_destination_after_claim(director):
    original = create_judge(director, "Snapshot Judge", "campaign-snapshot@example.org")
    destination = create_judge(
        director, "Destination Judge", "destination-primary@example.org",
        alternate_email="destination-alias@example.org",
    )
    campaign = ok(create_campaign(director, judge_ids=[original["id"]]))
    queue(director, campaign)
    (queued,) = campaign_outbox(director, campaign)
    assert queued["recipient_email"] == original["primary_email"]
    with SessionLocal() as db:
        # Exercise delivery with an Outbound address different from its snapshot.
        message = db.get(Outbound, UUID(queued["id"]))
        message.recipient_email = destination["alternate_email"]
        db.commit()
        claimed_id = claim(db, "campaign-delivery-dnc-test")
        assert claimed_id == message.id

    set_do_not_contact(director, destination, True)
    with SessionLocal() as db:
        deliver(db, claimed_id, "campaign-delivery-dnc-test")

    (message,) = campaign_outbox(director, campaign)
    assert message["recipient_email"] == destination["alternate_email"]
    assert_dnc_cancellation(director, message)
    (recipient,) = ok(director.get(f"/api/campaigns/{campaign['id']}/preview"))["items"]
    assert recipient["recipient_email"] == original["primary_email"]
    assert recipient["status"] == "cancelled"
    assert recipient["sent_at"] is None
    assert recipient["failure_reason"] == message["last_error"]


def test_judge_edit_without_dnc_preserves_suppression_and_previous_address(director):
    address = "suppressed-edit@example.org"
    campaign = ok(create_campaign(director, [{"email": address}]))
    queue(director, campaign)
    judge = create_judge(director, "Suppressed Judge", address, do_not_contact=True)
    edited = ok(director.patch(f"/api/judges/{judge['id']}", json={
        "display_name": "Renamed Judge", "primary_email": "new-suppressed-address@example.org",
    }))
    assert edited["do_not_contact"] is True
    assert address in edited["preserved_emails"]
    assert run_once("campaign-edit-dnc-test")
    (message,) = campaign_outbox(director, campaign)
    assert_dnc_cancellation(director, message)


def test_merged_linked_campaigns_stay_cancelled_but_fresh_alias_can_send(director):
    survivor = create_judge(director, "Surviving Judge", "surviving@example.org")
    former = create_judge(director, "Former Judge", "former@example.org")
    draft = ok(create_campaign(director, judge_ids=[former["id"]]))
    queued = ok(create_campaign(director, judge_ids=[former["id"]]))
    queue(director, queued)
    survivor = merge_contacts(director, survivor, former)
    assert survivor["do_not_contact"] is False
    stale_edit = director.patch(f"/api/judges/{former['id']}", json={
        "display_name": former["display_name"], "primary_email": "stale-edit@example.org", "do_not_contact": True,
    })
    assert stale_edit.status_code == 409

    queue(director, draft)
    assert campaign_outbox(director, draft) == []
    assert run_once("campaign-merged-contact-test")
    (message,) = campaign_outbox(director, queued)
    assert message["status"] == "cancelled"
    assert message["attempt_count"] == 0
    assert message["provider_message_id"] is None
    assert "merged" in message["last_error"].lower()
    for campaign in (draft, queued):
        (recipient,) = ok(director.get(f"/api/campaigns/{campaign['id']}/preview"))["items"]
        assert recipient["judge_id"] == former["id"]
        assert recipient["status"] == "cancelled"

    fresh = ok(create_campaign(director, [{"email": former["primary_email"]}]))
    (recipient,) = ok(director.get(f"/api/campaigns/{fresh['id']}/preview"))["items"]
    assert recipient["judge_id"] == survivor["id"]
    queue(director, fresh)
    assert run_once("campaign-merged-contact-test")
    (message,) = campaign_outbox(director, fresh)
    assert message["status"] == "sent"


def test_campaign_forward_and_reforward_recheck_dnc(director):
    campaign = ok(create_campaign(director, [{"email": "original-invite@example.org"}]))
    queue(director, campaign)
    assert run_once("campaign-forward-dnc-test")
    (original,) = campaign_outbox(director, campaign)
    first_forward = ok(director.post(f"/api/outbox/{original['id']}/forward", json={
        "recipient_email": "organizer@example.org",
    }))
    assert run_once("campaign-forward-dnc-test")
    judge = create_judge(
        director, "Suppressed Forward Recipient", "forward-primary@example.org",
        alternate_email="forward-alias@example.org", do_not_contact=True,
    )
    target = {"recipient_email": " Forward-Alias@EXAMPLE.ORG "}
    sources = [original["id"], first_forward["id"]]
    for source_id in sources:
        response = director.post(f"/api/outbox/{source_id}/forward", json=target)
        assert response.status_code == 409, response.text
        assert response.json()["detail"]["code"] == "outreach_blocked"
        assert "do not contact" in response.json()["detail"]["message"].lower()
    params = {"q": judge["alternate_email"]}
    rejected = ok(director.get("/api/outbox", params=params))["items"]
    assert {message["forwarded_from_id"] for message in rejected} == set(sources)
    for message in rejected:
        assert_dnc_cancellation(director, message)

    set_do_not_contact(director, judge, False)
    pending = [ok(director.post(f"/api/outbox/{source_id}/forward", json=target)) for source_id in sources]
    assert [message["forwarded_from_id"] for message in pending] == sources
    set_do_not_contact(director, judge, True)
    for _ in pending:
        assert run_once("campaign-forward-dnc-test")

    messages = {message["id"]: message for message in ok(director.get("/api/outbox", params=params))["items"]}
    for message in pending:
        assert_dnc_cancellation(director, messages[message["id"]])

    set_do_not_contact(director, judge, False)
    fresh = ok(director.post(f"/api/outbox/{first_forward['id']}/forward", json=target))
    assert run_once("campaign-forward-dnc-test")
    messages = {message["id"]: message for message in ok(director.get("/api/outbox", params=params))["items"]}
    assert messages[fresh["id"]]["status"] == "sent"
    assert messages[fresh["id"]]["recipient_email"] == judge["alternate_email"]
    assert all(messages[message["id"]]["status"] == "cancelled" for message in pending)
