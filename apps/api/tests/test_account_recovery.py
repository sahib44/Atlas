import json
from concurrent.futures import ThreadPoolExecutor
from datetime import timedelta
from threading import Barrier
from uuid import UUID

import pytest
from fastapi import HTTPException
from sqlalchemy import select

from mist.accounts import transition_lead
from mist import recovery
from mist.db import SessionLocal, now
from mist.models import AccountToken, Audit, Outbound, RecoveryMethod
from mist.worker import MockEmailProvider, run_once
from test_managed_accounts import (
    ACCOUNTS, HEAD_EMAIL, LEAD_EMAIL, NEW_PASSWORD, PASSWORD, POSITIONS,
    RECOVERY, RESET_COMPLETE, RESET_REQUEST, account_client, activate, backup_email,
    enroll_recovery, invite_and_activate, last_challenge, lead, managed, ok, send_invite, sign_in,
)


def method_request(client, destination, password=PASSWORD, kind="email"):
    return client.post(f"{RECOVERY}/methods", json={
        "kind": kind, "destination": destination, "password": password,
    })


def reset_request(client, email, destination, kind="email"):
    return client.post(RESET_REQUEST, json={"email": email, "kind": kind, "destination": destination})


def verify(client, token):
    return client.post(f"{RECOVERY}/verify", json={"token": token})


def methods(client):
    return ok(client.get(RECOVERY))["methods"]


def change_method(client, method_id, action, password=PASSWORD):
    return client.post(f"{RECOVERY}/methods/{method_id}/{action}", json={"password": password})


def test_existing_account_is_restricted_until_verified_and_can_logout(managed):
    with account_client() as account:
        assert ok(sign_in(account, LEAD_EMAIL))["recovery_required"]
        assert ok(account.get("/api/auth/me"))["recovery_required"]
        for endpoint in ("/api/dashboard", "/api/competitions", "/api/campaigns", "/api/tickets", POSITIONS):
            denied = account.get(endpoint)
            assert denied.status_code == 403
            assert denied.json()["detail"]["code"] == "recovery_required"
        assert account.patch("/api/auth/me", json={"full_name": "Fixture alternative"}).status_code == 403
        ok(account.post("/api/auth/logout"))
        assert account.get(RECOVERY).status_code == 401
        assert ok(sign_in(account, LEAD_EMAIL))["recovery_required"]
        ok(method_request(account, backup_email(LEAD_EMAIL)))
        token = last_challenge(LEAD_EMAIL, "verify_recovery")[1]
        assert account.get("/api/dashboard").status_code == 403
        ok(verify(account, token))
        assert verify(account, token).status_code >= 400
        assert not ok(account.get("/api/auth/me"))["recovery_required"]
        assert ok(account.get(POSITIONS))["items"]


def test_head_enrollment_enforces_policy_and_recent_password(lead):
    with account_client() as account, account_client() as outsider:
        ok(send_invite(lead))
        assert ok(activate(account, last_challenge(HEAD_EMAIL)[1]))["email"] == HEAD_EMAIL
        assert ok(sign_in(account, HEAD_EMAIL))["recovery_required"]
        assert not ok(account.get(RECOVERY))["allow_mist_email"]
        assert account.get(f"{ACCOUNTS}/department-dashboard?event_year=2027").status_code == 403
        assert outsider.get(RECOVERY).status_code == 401
        assert method_request(outsider, "fixture-backup@example.org").status_code == 401
        for invalid in (HEAD_EMAIL.upper(), "fixture.other@getmistified.com"):
            assert method_request(account, invalid).status_code >= 400
        assert method_request(account, "fixture-backup@example.org", password="incorrect").status_code >= 400
        assert method_request(account, "+12025550181", kind="sms").status_code >= 400
        ok(method_request(account, "fixture-backup@example.org"))
        method_id = methods(account)[0]["id"]
        token = last_challenge(HEAD_EMAIL, "verify_recovery")[1]
        assert verify(lead, token).status_code >= 400
        assert outsider.post(f"{RECOVERY}/verify", json={"token": token}).status_code == 401
        assert change_method(account, method_id, "preferred").status_code >= 400
        ok(verify(account, token))
        assert not ok(account.get("/api/auth/me"))["recovery_required"]
        assert ok(account.get(f"{ACCOUNTS}/department-dashboard?event_year=2027"))["items"]


def test_expires_unverified_methods_and_preserves_at_least_one_verified_method(lead):
    assert method_request(lead, LEAD_EMAIL).status_code >= 400
    ok(method_request(lead, "fixture.lead.personal@getmistified.com"))
    pending_id, token = last_challenge(LEAD_EMAIL, "verify_recovery")
    pending = next(method for method in methods(lead) if not method["verified"])
    existing = next(method for method in methods(lead) if method["verified"])
    assert change_method(lead, existing["id"], "remove").status_code >= 400
    assert change_method(lead, pending["id"], "resend").status_code == 429
    with SessionLocal.begin() as db:
        pending_token = db.get(AccountToken, pending_id)
        pending_token.created_at = now() - timedelta(minutes=30)
        pending_token.expires_at = now() - timedelta(seconds=1)
    assert verify(lead, token).status_code >= 400
    ok(change_method(lead, pending["id"], "resend"))
    new_token = last_challenge(LEAD_EMAIL, "verify_recovery")[1]
    assert new_token != token
    ok(verify(lead, new_token))
    assert change_method(lead, pending["id"], "preferred", "incorrect").status_code >= 400
    ok(change_method(lead, pending["id"], "preferred"))
    assert next(method for method in methods(lead) if method["id"] == pending["id"])["preferred"]
    ok(change_method(lead, existing["id"], "remove"))
    assert change_method(lead, pending["id"], "remove").status_code >= 400
    assert len(methods(lead)) == 1


@pytest.mark.parametrize("destination", ["fixture.personal@example.org", "fixture.personal@getmistified.com"])
def test_lead_reset_delivers_only_to_verified_selected_backup(managed, monkeypatch, destination):
    captures = []
    send = MockEmailProvider.send

    def capture(provider, message, *, body_text=None):
        captures.append((message.recipient_email, body_text))
        return send(provider, message, body_text=body_text)

    monkeypatch.setattr(MockEmailProvider, "send", capture)
    with account_client() as lead, account_client() as public:
        assert ok(sign_in(lead, LEAD_EMAIL))["recovery_required"]
        ok(method_request(lead, destination))
        _, verify_token = last_challenge(LEAD_EMAIL, "verify_recovery")
        assert run_once("fake-recovery-mail")
        assert len(captures) == 1
        assert captures[0][0] == destination
        assert f"/verify-recovery#token={verify_token}" in captures[0][1]
        ok(verify(lead, verify_token))
        for address, backup in (
            (LEAD_EMAIL, LEAD_EMAIL),
            ("unknown-fixture@getmistified.com", destination),
            (LEAD_EMAIL, "unknown-backup@example.org"),
        ):
            assert ok(reset_request(public, address, backup)) == {"ok": True}
        assert run_once("fake-recovery-mail")
        assert len(captures) == 1
        for _ in range(2):
            assert ok(reset_request(public, LEAD_EMAIL, destination)) == {"ok": True}
        assert run_once("fake-recovery-mail")
        assert len(captures) == 2
        reset_id, reset_token = last_challenge(LEAD_EMAIL, "reset")
        assert captures[1][0] == destination
        assert f"/reset-password#token={reset_token}" in captures[1][1]
        ok(public.post(RESET_COMPLETE, json={"token": reset_token, "password": NEW_PASSWORD}))
        assert lead.get("/api/auth/me").status_code == 401
        assert ok(sign_in(lead, LEAD_EMAIL, NEW_PASSWORD))["recovery_required"] is False
        assert public.post(RESET_COMPLETE, json={"token": reset_token, "password": PASSWORD}).status_code >= 400
        with SessionLocal() as db:
            stored = db.scalar(select(RecoveryMethod))
            assert destination not in str(stored.encrypted_destination)
            message = db.scalar(select(Outbound).where(Outbound.account_token_id == reset_id))
            assert destination != message.recipient_email
            assert destination not in message.body_text and reset_token not in message.body_text
            audit = json.dumps([(row.summary, row.after_data) for row in db.scalars(select(Audit))])
            assert verify_token not in audit and reset_token not in audit and destination not in audit


def test_lead_transition_keeps_identity_and_invalidates_old_recovery(lead, monkeypatch):
    original = ok(lead.get("/api/auth/me"))
    configured = "fixture.competitions@getmistified.com"
    with account_client() as head, account_client() as public:
        head_profile = invite_and_activate(lead, head)
        ok(reset_request(public, LEAD_EMAIL, backup_email(LEAD_EMAIL)))
        assert run_once("fixture-outgoing-lead-recovery")
        reset_id, old_token = last_challenge(LEAD_EMAIL, "reset")
        monkeypatch.setenv("MIST_COMPETITIONS_LEAD_EMAIL", configured)
        with SessionLocal() as db:
            with pytest.raises(HTTPException):
                transition_lead(db, PASSWORD)
        with SessionLocal() as db:
            transition_lead(db, NEW_PASSWORD)
        assert lead.get("/api/auth/me").status_code == 401
        assert ok(head.get("/api/auth/me"))["id"] == head_profile["id"]
        assert public.post(RESET_COMPLETE, json={"token": old_token, "password": PASSWORD}).status_code >= 400
        assert sign_in(lead, LEAD_EMAIL, PASSWORD).status_code == 401
        assert sign_in(lead, configured, PASSWORD).status_code == 401
        incoming = ok(sign_in(lead, configured, NEW_PASSWORD))
        assert incoming["id"] == original["id"] and incoming["recovery_required"]
        assert not methods(lead)
        assert lead.get(POSITIONS).status_code == 403
        with SessionLocal() as db:
            assert db.get(AccountToken, reset_id).revoked_at
            methods_from_previous_lead = db.scalars(select(RecoveryMethod).where(
                RecoveryMethod.user_id == UUID(original["id"]),
                RecoveryMethod.removed_at.is_(None),
            )).all()
            assert not methods_from_previous_lead
        enroll_recovery(lead, configured, "fixture.incoming@example.org", NEW_PASSWORD)
        assert ok(lead.get(POSITIONS))["items"]


def test_configured_organizational_mailbox_cannot_recover_placeholder_lead(managed, monkeypatch):
    shared_mailbox = "fixture.organizational@getmistified.com"
    with account_client() as lead, account_client() as public:
        ok(sign_in(lead, LEAD_EMAIL))
        enroll_recovery(lead, LEAD_EMAIL, shared_mailbox)
        ok(reset_request(public, LEAD_EMAIL, shared_mailbox))
        assert run_once("fixture-reserve-shared-mailbox")
        _, prior_token = last_challenge(LEAD_EMAIL, "reset")

        monkeypatch.setenv("MIST_COMPETITIONS_LEAD_EMAIL", shared_mailbox)
        assert ok(lead.get("/api/auth/me"))["recovery_required"]
        assert method_request(lead, shared_mailbox).status_code == 422
        assert method_request(lead, "fixture.organizational+backup@getmistified.com").status_code == 422
        assert public.post(RESET_COMPLETE, json={"token": prior_token, "password": NEW_PASSWORD}).status_code >= 400
        enroll_recovery(lead, LEAD_EMAIL, "fixture.separate@example.org")
        assert not ok(lead.get("/api/auth/me"))["recovery_required"]


def test_two_concurrent_removals_cannot_remove_all_verified_methods(lead):
    enroll_recovery(lead, LEAD_EMAIL, "fixture.second@example.org")
    ids = [method["id"] for method in methods(lead)]
    assert len(ids) == 2
    barrier = Barrier(2)

    def remove(method_id):
        with account_client() as account:
            ok(sign_in(account, LEAD_EMAIL))
            barrier.wait(timeout=10)
            return change_method(account, method_id, "remove").status_code

    with ThreadPoolExecutor(max_workers=2) as pool:
        outcomes = list(pool.map(remove, ids))
    assert outcomes.count(200) == 1
    assert len(methods(lead)) == 1 and methods(lead)[0]["verified"]
    assert not ok(lead.get("/api/auth/me"))["recovery_required"]


def test_sms_recovery_uses_only_configured_test_adapter(managed, monkeypatch):
    messages = []

    class FakeSms:
        def send(self, *, destination, body_text, idempotency_key):
            messages.append((destination, body_text))
            return f"fixture-sms-{idempotency_key}"

    monkeypatch.setattr(recovery, "get_sms_provider", lambda: FakeSms())
    phone = "+12025550181"
    with account_client() as account, account_client() as public:
        assert ok(sign_in(account, LEAD_EMAIL))["recovery_required"]
        assert ok(account.get("/api/auth/account-mode"))["sms_available"]
        assert ok(account.get(RECOVERY))["sms_available"]
        ok(method_request(account, phone, kind="sms"))
        _, verification = last_challenge(LEAD_EMAIL, "verify_recovery")
        assert run_once("fixture-sms")
        assert len(messages) == 1 and messages[0][0] == phone
        assert f"/verify-recovery#token={verification}" in messages[0][1]
        ok(verify(account, verification))
        assert methods(account)[0]["kind"] == "sms"
        assert phone not in methods(account)[0]["masked_destination"]
        ok(reset_request(public, LEAD_EMAIL, phone, kind="sms"))
        assert run_once("fixture-sms")
        _, token = last_challenge(LEAD_EMAIL, "reset")
        assert len(messages) == 2 and messages[1][0] == phone
        assert f"/reset-password#token={token}" in messages[1][1]
        ok(public.post(RESET_COMPLETE, json={"token": token, "password": NEW_PASSWORD}))
        assert account.get("/api/auth/me").status_code == 401
