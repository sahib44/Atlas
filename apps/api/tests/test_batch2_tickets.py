from concurrent.futures import ThreadPoolExecutor
from threading import Barrier
from uuid import UUID, uuid4

import pytest
from fastapi.testclient import TestClient

from mist.db import SessionLocal
from mist.main import app
from mist.models import Ticket, TicketDraft
from mist.worker import run_once
from test_ticket_flow import create, login, ok, request_review


QUOTE = "Can you help with the rules?"
PRIVATE_NOTE = "Private source context."
UNSELECTED_REPLY = "Public reply to the requester."


def ticket_outbox(client, ticket_id):
    return ok(client.get("/api/outbox", params={"ticket_id": ticket_id}))["items"]


@pytest.fixture(params=["ticket", "outbox"])
def forward_paths(request, director):
    ticket = create(director)
    ticket_url = f"/api/tickets/{ticket['id']}"
    original = ok(director.get(f"{ticket_url}/messages"))["items"][0]
    ok(director.post(f"{ticket_url}/notes", json={
        "body": PRIVATE_NOTE, "request_id": str(uuid4()),
    }))
    reply = ok(director.post(f"{ticket_url}/messages", json={
        "body": UNSELECTED_REPLY, "request_id": str(uuid4()),
    }))
    ticket_forward = f"{ticket_url}/messages/{original['id']}/forward"
    if request.param == "ticket":
        alternate = f"{ticket_url}/messages/{reply['id']}/forward"
        return ticket, ticket_forward, alternate, "ticket.message_forwarded"

    first_forward = ok(director.post(ticket_forward, json={
        "recipient_email": "source@example.org",
    }))
    assert run_once("batch2-ticket-source")
    assert run_once("batch2-ticket-source")
    return (
        ticket,
        f"/api/outbox/{first_forward['id']}/forward",
        f"/api/outbox/{reply['outbound_message_id']}/forward",
        "message.forwarded",
    )


def test_forward_retries_reuse_outbound_but_fresh_ids_allow_identical_sends(director, forward_paths):
    ticket, path, alternate, action = forward_paths
    ticket_id = ticket["id"]
    existing = {item["id"] for item in ticket_outbox(director, ticket_id)}
    payload = {
        "recipient_email": "Organizers@EXAMPLE.ORG", "note": "Please review.",
        "request_id": str(uuid4()),
    }
    normalized_payload = {
        **payload, "recipient_email": " organizers@example.org ", "note": " Please review. ",
    }
    barrier = Barrier(2)
    cookies = dict(director.cookies)

    def attempt(data):
        with TestClient(app, cookies=cookies, headers={"X-MIST-Request": "1"}) as client:
            barrier.wait(timeout=10)
            return ok(client.post(path, json=data))

    with ThreadPoolExecutor(max_workers=2) as pool:
        requests = [pool.submit(attempt, data) for data in (payload, normalized_payload)]
        first, replay = (request.result(timeout=15) for request in requests)
    assert first["id"] == replay["id"]
    assert first["recipient_email"] == "organizers@example.org"
    assert first["related_ticket_id"] == ticket_id
    assert QUOTE in first["body_text"] and payload["note"] in first["body_text"]
    assert PRIVATE_NOTE not in first["body_text"] and UNSELECTED_REPLY not in first["body_text"]

    for url, change in (
        (path, {"recipient_email": "another@example.org"}),
        (path, {"note": "A different note."}),
        (alternate, {}),
    ):
        rejected = director.post(url, json={**payload, **change})
        assert rejected.status_code == 409, rejected.text
        assert rejected.json()["detail"]["code"] == "request_reused"

    second = ok(director.post(path, json={**payload, "request_id": str(uuid4())}))
    assert second["id"] != first["id"]
    assert second["body_text"] == first["body_text"]
    assert second["recipient_email"] == first["recipient_email"]
    activity = ok(director.get(f"/api/tickets/{ticket_id}/activity"))["items"]
    assert sum(event["action"] == action for event in activity) == 2

    for _ in range(3):
        if not run_once("batch2-ticket-forward"):
            break
    sent = {
        item["id"]: item for item in ticket_outbox(director, ticket_id)
        if item["id"] not in existing
    }
    assert set(sent) == {first["id"], second["id"]}
    assert all(item["status"] == "sent" and item["attempt_count"] == 1 for item in sent.values())
    receipts = {item["provider_message_id"] for item in sent.values()}
    assert len(receipts) == 2 and all(receipt.startswith("mock-") for receipt in receipts)
    assert ok(director.post(path, json=normalized_payload))["id"] == first["id"]

    legacy = {"recipient_email": payload["recipient_email"], "note": payload["note"]}
    legacy_ids = {ok(director.post(path, json=legacy))["id"] for _ in range(2)}
    assert len(legacy_ids) == 2 and legacy_ids.isdisjoint(sent)


def test_forward_retry_rechecks_requester_permission_and_rejects_malformed_recipient(director, forward_paths):
    ticket, path, _, _ = forward_paths
    ticket_id = ticket["id"]
    with SessionLocal() as db:
        db.get(Ticket, UUID(ticket_id)).requester_email = "Requester@EXAMPLE.ORG"
        db.commit()

    chair = login(director, "chair")
    payload = {"recipient_email": " requester@Example.org ", "request_id": str(uuid4())}
    allowed = ok(director.post(path, json=payload))
    assert allowed["recipient_email"] == "requester@example.org"
    existing = {item["id"] for item in ticket_outbox(director, ticket_id)}
    login(director, "director")
    ok(director.patch(f"/api/users/{chair['id']}", json={
        "role": "competitions_member", "permissions": ["campaign_approver"], "is_active": True,
    }))
    login(director, "chair")

    for attempt in (payload, {"recipient_email": payload["recipient_email"]}):
        rejected = director.post(path, json=attempt)
        assert rejected.status_code == 403, rejected.text
        assert rejected.json()["detail"]["code"] == "chair_approval_required"
    for address in (
        "one@example.org,two@example.org",
        "one@example.org\r\nBcc: two@example.org",
    ):
        rejected = director.post(path, json={"recipient_email": address, "request_id": str(uuid4())})
        assert rejected.status_code == 400, rejected.text
        assert rejected.json()["detail"]["code"] == "invalid_contact_email"
    assert {item["id"] for item in ticket_outbox(director, ticket_id)} == existing


@pytest.mark.parametrize("stale", ["demoted", "restored", "inactive", "missing_approver", "missing_timestamp"])
def test_reply_requires_approval_from_currently_authorized_approver(director, stale):
    ticket = create(director)
    ticket_url = f"/api/tickets/{ticket['id']}"
    login(director, "competitions")
    review = request_review(director, ticket)
    chair = login(director, "chair")
    approved = ok(director.post(f"{ticket_url}/drafts/{review['id']}/approve"))
    assert approved["approved_by"] == chair["id"] and approved["approved_at"]

    login(director, "director")
    if stale in ("demoted", "restored", "inactive"):
        ok(director.patch(f"/api/users/{chair['id']}", json={
            "role": "competitions_member" if stale in ("demoted", "restored") else chair["role"],
            "permissions": ["campaign_approver"],
            "is_active": stale != "inactive",
        }))
        if stale == "restored":
            ok(director.patch(f"/api/users/{chair['id']}", json={
                "role": chair["role"], "permissions": [], "is_active": True,
            }))
    else:
        with SessionLocal() as db:
            draft = db.get(TicketDraft, UUID(review["id"]))
            if stale == "missing_approver":
                draft.approved_by = None
            else:
                draft.approved_at = None
            db.commit()

    login(director, "competitions")
    request_id = str(uuid4())
    rejected = director.post(f"{ticket_url}/messages", json={
        "body": review["body"], "approval_id": review["id"], "request_id": request_id,
    })
    assert rejected.status_code == 403, rejected.text
    assert rejected.json()["detail"]["code"] == "chair_approval_required"
    assert not ticket_outbox(director, ticket["id"])
    assert ok(director.get(f"{ticket_url}/messages"))["total"] == 1
    assert ok(director.get(ticket_url))["version"] == ticket["version"]
    with SessionLocal() as db:
        assert db.get(TicketDraft, UUID(review["id"])).status != "used"

    if stale == "missing_approver":
        drafts = ok(director.get(f"{ticket_url}/drafts"))["items"]
        assert drafts[0]["id"] == review["id"] and drafts[0]["status"] == "superseded"
        replacement = request_review(director, ticket, body=review["body"])
        assert replacement["id"] != review["id"]
        login(director, "chair")
        ok(director.post(f"{ticket_url}/drafts/{replacement['id']}/approve"))
        login(director, "competitions")
        ok(director.post(f"{ticket_url}/messages", json={
            "body": replacement["body"], "approval_id": replacement["id"], "request_id": request_id,
        }))
        assert [item["body_text"] for item in ticket_outbox(director, ticket["id"])] == [review["body"]]


def test_approved_reply_cannot_also_save_an_unreviewed_private_note(director):
    ticket = create(director)
    ticket_url = f"/api/tickets/{ticket['id']}"
    login(director, "competitions")
    review = request_review(director, ticket, target="resolved", resolution="Checked with the rules team.")
    approver = login(director, "director")
    approved = ok(director.post(f"{ticket_url}/drafts/{review['id']}/approve"))
    assert approved["approved_by"] == approver["id"]

    login(director, "competitions")
    payload = {
        "status": "resolved", "reply_body": review["body"],
        "resolution_note": review["resolution_note"], "approval_id": review["id"],
        "request_id": str(uuid4()), "expected_version": ticket["version"],
    }
    rejected = director.post(f"{ticket_url}/status", json={
        **payload, "internal_note": "Additional context the director did not review.",
    })
    assert rejected.status_code == 409, rejected.text
    assert rejected.json()["detail"]["code"] == "approval_changed"
    assert not ticket_outbox(director, ticket["id"])
    assert ok(director.get(f"{ticket_url}/notes"))["total"] == 0
    unchanged = ok(director.get(ticket_url))
    assert unchanged["status"] == "new" and unchanged["version"] == ticket["version"]

    result = ok(director.post(f"{ticket_url}/status", json=payload))
    assert result["status"] == "resolved"
    assert [item["body_text"] for item in ticket_outbox(director, ticket["id"])] == [review["body"]]
    notes = ok(director.get(f"{ticket_url}/notes"))["items"]
    assert [(note["kind"], note["body"]) for note in notes] == [("resolution", review["resolution_note"])]
    private_note = ok(director.post(f"{ticket_url}/notes", json={
        "body": "A separately saved private note.", "request_id": str(uuid4()),
    }))
    assert private_note["kind"] == "internal"
    assert [item["body_text"] for item in ticket_outbox(director, ticket["id"])] == [review["body"]]
