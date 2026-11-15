from uuid import uuid4
from sqlalchemy import select
from mist.db import SessionLocal
from mist.models import Ticket, User
from mist.auth import hasher
from mist.worker import run_once
from test_ticket_flow import login


def ok(response):
    assert response.status_code == 200, response.text
    return response.json()


def new_ticket(client, **extra):
    return client.post("/api/tickets", json={"requester_name": "Sample Sender", "requester_email": "sender@example.org", "subject": "Question for competitions", "body": "Public original email", **extra})


def test_ticket_form_and_api_only_need_sender_and_message(director):
    ticket = ok(new_ticket(director))
    assert not {"priority", "committee_id", "category_id", "assigned_user_id"}.intersection(ticket)
    for field, value in [("priority", "urgent"), ("committee_id", str(uuid4())), ("category_id", str(uuid4()))]:
        assert new_ticket(director, **{field: value}).status_code == 422
    assert director.post(f"/api/tickets/{ticket['id']}/status", json={"status": "assigned"}).status_code == 422
    assert director.post(f"/api/tickets/{ticket['id']}/status", json={"status": "resolved", "priority": "high"}).status_code == 422
    dashboard = ok(director.get("/api/dashboard"))
    assert "Tickets by committee" not in dashboard["groups"]
    assert not any(metric["label"] == "Unassigned tickets" for metric in dashboard["metrics"])


def test_forward_public_ticket_message_excludes_private_notes(director):
    for address in ("registration@example.org", "organizer@example.org"):
        ok(director.post("/api/judges", json={
            "display_name": "Sample Ticket Recipient", "primary_email": address,
            "do_not_contact": True,
        }))
    ticket = ok(new_ticket(director))
    ok(director.post(f"/api/tickets/{ticket['id']}/notes", json={"body": "Keep this private", "request_id": str(uuid4())}))
    original = ok(director.get(f"/api/tickets/{ticket['id']}/messages"))["items"][0]
    result = ok(director.post(f"/api/tickets/{ticket['id']}/messages/{original['id']}/forward", json={"recipient_email": "registration@example.org", "note": "Can your team help?"}))
    assert result["recipient_email"] == "registration@example.org"
    assert run_once("ticket-forward-dnc-test")

    forward_url = f"/api/outbox/{result['id']}/forward"
    competitions = ok(director.get("/api/competitions"))["items"]
    pending_contact = "pending-ticket-forward@example.org"
    ok(director.post("/api/recruitment", json={
        "competition_id": competitions[0]["id"], "name": "Pending Sample Judge", "email": pending_contact,
        "qualifications": "Has judged youth arts competitions.",
    }))
    blocked = director.post(forward_url, json={"recipient_email": pending_contact})
    assert blocked.status_code == 409
    assert blocked.json()["detail"]["code"] == "outreach_blocked"

    forwarded = ok(director.post(forward_url, json={"recipient_email": " Organizer@EXAMPLE.ORG "}))
    assert forwarded["recipient_email"] == "organizer@example.org"
    assert forwarded["forwarded_from_id"] == result["id"]
    assert run_once("ticket-forward-dnc-test")

    messages = ok(director.get("/api/outbox", params={"ticket_id": ticket["id"]}))["items"]
    assert {message["id"] for message in messages} == {result["id"], forwarded["id"]}
    for message in messages:
        assert message["related_ticket_id"] == ticket["id"]
        assert message["status"] == "sent"
        assert message["provider_message_id"].startswith("mock-")
        assert "Public original email" in message["body_text"]
        assert "Can your team help?" in message["body_text"]
        assert "Keep this private" not in message["body_text"]

    other = ok(new_ticket(director, subject="Another email"))
    assert director.post(f"/api/tickets/{other['id']}/messages/{original['id']}/forward", json={"recipient_email": "registration@example.org"}).status_code == 404
    assert ok(director.get(f"/api/tickets/{ticket['id']}"))["status"] == "new"
    login(director, "tech")
    assert ok(director.get("/api/outbox", params={"ticket_id": ticket["id"]}))["total"] == 0
    assert director.post(forward_url, json={"recipient_email": "another@example.org"}).status_code == 400


def test_retired_support_account_cannot_sign_in_even_if_active(client):
    with SessionLocal() as db:
        db.add(User(full_name="Legacy support", email="legacy@example.org", role="support_staff", is_active=True, permissions=[], password_hash=hasher.hash("legacy-test-password")))
        db.commit()
    response = client.post("/api/auth/login", json={"email": "legacy@example.org", "password": "legacy-test-password"})
    assert response.status_code == 401
