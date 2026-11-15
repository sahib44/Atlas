from uuid import uuid4

from mist.worker import run_once
from test_directory_email import create_campaign
from test_ticket_flow import create, login, ok, request_review
from test_workflows import queue


def test_restricted_ticket_delivery_is_absent_from_audit_dashboard_and_job_counts(director):
    ticket = create(director)
    reply = ok(director.post(f"/api/tickets/{ticket['id']}/messages", json={
        "body": "Sample private ticket correspondence.", "request_id": str(uuid4()),
    }))
    message_id = reply["outbound_message_id"]
    audit_path = "/api/audit-events?q=message.delivery_attempt"
    campaign = ok(create_campaign(director, [{"email": "unrestricted@example.org"}]))
    queue(director, campaign)
    tech = login(director, "tech")
    assert ok(director.get("/api/system"))["queue"] == {"queued": 1}
    login(director, "director")
    def set_access(permissions):
        ok(director.patch(f"/api/users/{tech['id']}", json={
            "role": tech["role"], "is_active": True, "permissions": permissions,
        }))

    set_access(["private_tickets"])
    login(director, "tech")
    assert ok(director.get("/api/system"))["queue"] == {"queued": 2}
    assert run_once("ticket-audit-privacy")
    assert run_once("ticket-audit-privacy")
    assert any(row["entity_id"] == message_id for row in ok(director.get(audit_path))["items"])
    login(director, "director")
    set_access([])
    login(director, "tech")
    audit = ok(director.get(audit_path))
    assert audit["total"] == 1 and all(row["entity_id"] != message_id for row in audit["items"])
    assert ok(director.get("/api/audit-events?q=ticket.replied"))["total"] == 0
    assert message_id not in director.get("/api/dashboard").text
    assert ok(director.get(f"/api/outbox?ticket_id={ticket['id']}"))["total"] == 0


def test_demoted_chair_loses_approval_alerts_but_keeps_ticket_access(director):
    ticket = create(director)
    login(director, "competitions")
    request_review(director, ticket)
    chair = login(director, "chair")
    alerts = ok(director.get("/api/notifications"))["items"]
    approval = next(row for row in alerts if row["kind"] == "ticket_approval_requested")
    login(director, "director")
    ok(director.patch(f"/api/users/{chair['id']}", json={
        "role": "competitions_member", "permissions": [], "is_active": True,
    }))
    login(director, "chair")
    assert ok(director.get(f"/api/tickets/{ticket['id']}"))["id"] == ticket["id"]
    assert all(row["id"] != approval["id"] for row in ok(director.get("/api/notifications"))["items"])
    assert director.post(f"/api/notifications/{approval['id']}/read").status_code == 404
