from uuid import uuid4

import pytest
from sqlalchemy import func, select

from mist.db import SessionLocal
from mist.models import Audit, Notification, User
from mist.worker import run_once
from test_ticket_flow import create, login, ok


APPROVED_BODY = "Please submit the score sheet by Friday."
PRIVATE_BODY = "Private review context. Do not disclose."


def draft(client, path, *, kind="notes", body=APPROVED_BODY, **fields):
    version = ok(client.get(path))["version"]
    return ok(client.post(f"{path}/drafts", json={
        "kind": kind,
        "body": body,
        "expected_version": version,
        "request_id": str(uuid4()),
        **fields,
    }))


def approval_path(path, review):
    return f"{path}/drafts/{review['id']}/approve"


def approve_for_reply(client, path, review):
    login(client, "chair")
    approved = ok(client.post(approval_path(path, review), json={"allow_public_reply": True}))
    assert approved["public_reply_approved"] is True
    login(client, "competitions")
    return approved


def approve_internal(client, path, review):
    login(client, "chair")
    approved = ok(client.post(approval_path(path, review)))
    assert approved["public_reply_approved"] is False
    login(client, "competitions")


def reply_payload(review, **fields):
    return {
        "approval_id": review["id"],
        "body": review["body"],
        "reply_subject": review["reply_subject"],
        "request_id": str(uuid4()),
        **fields,
    }


def test_reuse_approved_note_preserves_private_history_and_sends_once(director):
    with SessionLocal.begin() as db:
        db.add(User(
            email="fixture-managed-chair@getmistified.com", full_name="Fixture Managed Chair",
            role="competitions_chair", password_hash="unused-fixture",
            permissions=[], managed_account=True, is_active=True,
        ))
    login(director, "competitions")
    ticket = ok(director.post("/api/tickets", json={
        "requester_name": "Sample Requester", "requester_email": "requester@example.org",
        "subject": "Rules question <b></b>", "body": "Can you help with the rules?",
    }))
    path = f"/api/tickets/{ticket['id']}"
    original = draft(director, path)
    assert original["reply_subject"] == ok(director.get(path))["reply_subject"]
    assert original["reply_subject"] == f"Re: {ticket['public_number']} — Rules question"
    assert draft(director, path)["id"] == original["id"]
    assert director.post(approval_path(path, original), json={"allow_public_reply": True}).status_code == 403
    approved = approve_for_reply(director, path, original)

    login(director, "chair")
    assert ok(director.post(approval_path(path, original), json={"allow_public_reply": True}))["id"] == approved["id"]
    login(director, "competitions")
    assert ok(director.get("/api/outbox", params={"ticket_id": ticket["id"]}))["total"] == 0

    written = ok(director.post(f"{path}/notes", json={
        "body": approved["body"], "approval_id": approved["id"], "request_id": str(uuid4()),
    }))
    ok(director.post(f"{path}/notes", json={"body": PRIVATE_BODY, "request_id": str(uuid4())}))
    version = ok(director.get(path))["version"]
    review = ok(director.get(f"{path}/drafts"))["items"][0]
    assert review["id"] == approved["id"]
    assert review["status"] == "approved"
    assert review["base_version"] == version
    assert draft(director, path, kind="messages", target_status="waiting_on_requester",
                 reply_subject=approved["reply_subject"])["id"] == approved["id"]

    mixed = director.post(f"{path}/status", json={
        "status": "waiting_on_requester",
        "reply_body": APPROVED_BODY,
        "reply_subject": approved["reply_subject"],
        "internal_note": PRIVATE_BODY,
        "approval_id": approved["id"],
        "expected_version": version,
    })
    assert mixed.status_code == 409

    payload = reply_payload(approved)
    public = ok(director.post(f"{path}/messages", json=payload))
    assert ok(director.post(f"{path}/messages", json=payload))["id"] == public["id"]
    for updated in (
        {**payload, "reply_subject": "Changed during retry"},
        {**payload, "request_id": str(uuid4())},
    ):
        assert director.post(f"{path}/messages", json=updated).status_code in {403, 409}
    assert run_once("ticket-reused-approval")

    replies = ok(director.get(f"{path}/messages"))["items"]
    assert [item["body"] for item in replies] == ["Can you help with the rules?", APPROVED_BODY]
    assert [item["message_type"] for item in replies] == ["requester_message", "staff_reply"]
    private_notes = ok(director.get(f"{path}/notes"))["items"]
    assert any(item["id"] == written["id"] and item["body"] == APPROVED_BODY for item in private_notes)
    assert {item["body"] for item in private_notes} == {APPROVED_BODY, PRIVATE_BODY}
    history = ok(director.get(f"{path}/drafts"))["items"]
    assert len(history) == 1 and history[0]["id"] == original["id"]
    assert history[0]["status"] == "used" and history[0]["kind"] == "notes"
    assert history[0]["body"] == original["body"]

    outbox = ok(director.get("/api/outbox", params={"ticket_id": ticket["id"]}))["items"]
    assert len(outbox) == 1
    assert outbox[0]["recipient_email"] == ticket["requester_email"]
    assert outbox[0]["subject"] == approved["reply_subject"]
    assert outbox[0]["body_text"] == APPROVED_BODY and outbox[0]["attempt_count"] == 1

    with SessionLocal() as db:
        for action in ("ticket.approval_requested", "ticket.draft_approved", "ticket.replied"):
            assert db.scalar(select(func.count(Audit.id)).where(
                Audit.entity_id == ticket["id"], Audit.action == action,
            )) == 1
        for kind in ("ticket_approval_requested", "ticket_draft_approved"):
            assert db.scalar(select(func.count(Notification.id)).where(
                Notification.ticket_id == ticket["id"], Notification.kind == kind,
            )) == 1


@pytest.mark.parametrize("change", [{"body": "An edited message."}, {"reply_subject": "An edited subject"}])
def test_changed_note_reply_content_requires_fresh_approval(director, change):
    login(director, "competitions")
    ticket = create(director)
    path = f"/api/tickets/{ticket['id']}"
    approved = approve_for_reply(director, path, draft(director, path))
    if "reply_subject" in change:
        newer_ticket = create(director)
        change = {"reply_subject": f"See also {newer_ticket['public_number']}"}
    assert director.post(f"{path}/messages", json=reply_payload(approved, **change)).status_code == 409
    assert ok(director.get("/api/outbox", params={"ticket_id": ticket["id"]}))["total"] == 0
    revised = draft(director, path, kind="messages", target_status="waiting_on_requester", **change)
    assert revised["id"] != approved["id"] and revised["status"] == "pending_approval"
    assert director.post(f"{path}/messages", json=reply_payload(revised)).status_code == 403
    approve_internal(director, path, revised)
    ok(director.post(f"{path}/messages", json=reply_payload(revised)))
    outbox = ok(director.get("/api/outbox", params={"ticket_id": ticket["id"]}))
    assert outbox["total"] == 1
    outgoing_subject = outbox["items"][0]["subject"]
    assert outgoing_subject == revised["reply_subject"]
    if "reply_subject" in change:
        assert outgoing_subject == f"Re: {ticket['public_number']} — {change['reply_subject']}"
        received = ok(director.post("/api/inbox/simulate", json={
            "sender_email": ticket["requester_email"], "subject": f"Re: {outgoing_subject}",
            "body": "Thanks for the updated guidance.", "request_id": str(uuid4()),
        }))
        assert received["ticket_id"] == ticket["id"]


def test_private_approval_and_status_note_cannot_authorize_public_reply(director):
    login(director, "competitions")
    ticket = create(director)
    path = f"/api/tickets/{ticket['id']}"
    review = draft(director, path)
    approve_internal(director, path, review)
    assert director.post(f"{path}/messages", json=reply_payload(review)).status_code in {403, 409}
    assert draft(director, path, kind="messages", target_status="waiting_on_requester")["id"] != review["id"]

    status_note = draft(director, path, target_status="waiting_on_organizer")
    login(director, "chair")
    assert director.post(approval_path(path, status_note), json={"allow_public_reply": True}).status_code in {400, 409, 422}
    assert ok(director.get("/api/outbox", params={"ticket_id": ticket["id"]}))["total"] == 0


def test_public_permission_stays_bound_to_ticket_author_status_and_thread_version(director):
    login(director, "competitions")
    ticket = create(director)
    path = f"/api/tickets/{ticket['id']}"
    approved = approve_for_reply(director, path, draft(director, path))
    second = create(director)
    assert director.post(f"/api/tickets/{second['id']}/messages", json=reply_payload(approved)).status_code == 403
    assert director.post(f"{path}/status", json={
        "status": "resolved", "reply_body": APPROVED_BODY, "reply_subject": approved["reply_subject"],
        "resolution_note": PRIVATE_BODY, "approval_id": approved["id"],
    }).status_code == 409
    login(director, "director")
    assert director.post(f"{path}/messages", json=reply_payload(approved)).status_code == 403
    received = ok(director.post("/api/inbox/simulate", json={
        "sender_email": ticket["requester_email"], "subject": f"Re: {ticket['public_number']}",
        "body": "New context from the requester.", "request_id": str(uuid4()),
    }))
    assert received["ticket_id"] == ticket["id"]
    login(director, "competitions")
    assert director.post(f"{path}/messages", json=reply_payload(approved)).status_code == 403
    assert ok(director.get("/api/outbox", params={"ticket_id": ticket["id"]}))["total"] == 0
