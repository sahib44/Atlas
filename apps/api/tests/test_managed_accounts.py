import hashlib
import json
import os
from concurrent.futures import ThreadPoolExecutor
from datetime import timedelta
from secrets import token_urlsafe
from threading import Barrier
from uuid import UUID

import pytest
from fastapi import HTTPException
from fastapi.testclient import TestClient
from sqlalchemy import func, select

from mist.accounts import bootstrap_lead, raw_account_token
from mist.auth import hasher
from mist.db import SessionLocal, now
from mist.main import app
from mist.models import AccountResetRequest, AccountToken, Audit, DepartmentAssignment, Outbound, Session, User
from mist.worker import MockEmailProvider, run_once


ORIGIN = "https://atlas.example.test"
LEAD_EMAIL = "fixture.lead@getmistified.com"
HEAD_EMAIL = "fixture.head@getmistified.com"
REPLACEMENT_EMAIL = "fixture.replacement@getmistified.com"
PASSWORD = "fixture-correct-password"
NEW_PASSWORD = "fixture-replacement-password"
ACCOUNTS = "/api/accounts"
POSITIONS = f"{ACCOUNTS}/positions"
RESET_REQUEST = f"{ACCOUNTS}/password-reset/request"
RESET_COMPLETE = f"{ACCOUNTS}/password-reset/complete"
RECOVERY = f"{ACCOUNTS}/recovery"


@pytest.fixture
def managed(monkeypatch):
    monkeypatch.setenv("MIST_ACCOUNT_MODE", "managed")
    monkeypatch.setenv("MIST_PUBLIC_ORIGIN", ORIGIN)
    monkeypatch.setenv("MIST_ACCOUNT_TOKEN_KEY", token_urlsafe(48))
    monkeypatch.setenv("MIST_RECOVERY_KEY", token_urlsafe(48))
    monkeypatch.setenv("MIST_COMPETITIONS_LEAD_EMAIL", LEAD_EMAIL)
    monkeypatch.setenv("COOKIE_SECURE", "true")
    with SessionLocal() as db:
        bootstrap_lead(db, LEAD_EMAIL, "Fixture Lead", PASSWORD)


def account_client():
    return TestClient(app, base_url=ORIGIN, headers={"X-MIST-Request": "1"})


@pytest.fixture
def lead(managed):
    with account_client() as client:
        user = ok(sign_in(client, LEAD_EMAIL))
        assert user["role"] == "competitions_chair"
        assert user["department"] is None
        enroll_recovery(client, LEAD_EMAIL)
        yield client


def ok(response):
    assert response.status_code == 200, response.text
    return response.json()


def sign_in(client, email, password=PASSWORD):
    return client.post("/api/auth/login", json={"email": email, "password": password})


def position(client, slug):
    return next(item for item in ok(client.get(POSITIONS))["items"] if item["slug"] == slug)


def send_invite(client, slug="arts", email=HEAD_EMAIL):
    return client.post(f"{POSITIONS}/{slug}/invite", json={"email": email})


def position_action(client, slug, action, assignment_id, **fields):
    return client.post(f"{POSITIONS}/{slug}/{action}", json={"assignment_id": assignment_id, **fields})


def last_challenge(email, purpose="activate"):
    with SessionLocal() as db:
        query = select(AccountToken).where(AccountToken.purpose == purpose)
        if purpose == "activate":
            query = query.where(AccountToken.email == email)
        else:
            query = query.join(User, AccountToken.user_id == User.id).where(User.email == email)
        challenge = db.scalar(query.order_by(AccountToken.created_at.desc()).limit(1))
        assert challenge is not None
        return challenge.id, raw_account_token(challenge)


def activate(client, token, **fields):
    return client.post(f"{ACCOUNTS}/activate", json={
        "token": token, "full_name": "Fixture Head", "password": PASSWORD, **fields,
    })


def invite_and_activate(lead, client, slug="arts", email=HEAD_EMAIL):
    ok(send_invite(lead, slug, email))
    ok(activate(client, last_challenge(email)[1]))
    ok(sign_in(client, email))
    enroll_recovery(client, email)
    return ok(client.get("/api/auth/me"))


def backup_email(email):
    return email.partition("@")[0] + "@example.org"


def enroll_recovery(client, email, destination=None, password=PASSWORD):
    ok(client.post(f"{RECOVERY}/methods", json={
        "kind": "email", "destination": destination or backup_email(email), "password": password,
    }))
    ok(client.post(f"{RECOVERY}/verify", json={"token": last_challenge(email, "verify_recovery")[1]}))


def test_only_bootstrapped_lead_can_manage_positions(lead, monkeypatch):
    assert ok(lead.get("/api/auth/account-mode")) == {"managed": True, "sms_available": False}
    positions = ok(lead.get(POSITIONS))["items"]
    assert {row["slug"] for row in positions} == {
        "arts", "knowledge-quran", "writing-oratory", "brackets", "group-projects",
        "brothers-sports", "sisters-sports",
    }
    assert all(row["state"] == "unassigned" and not row["email"] for row in positions)
    with SessionLocal() as db:
        existing = db.scalar(select(func.count()).select_from(User).where(User.managed_account, User.role == "competitions_chair"))
        assert existing == 1
        with pytest.raises(HTTPException) as duplicate:
            bootstrap_lead(db, LEAD_EMAIL, "Fixture Second Lead", PASSWORD)
        assert duplicate.value.status_code == 409

    with account_client() as outsider:
        assert outsider.get(POSITIONS).status_code == 401
        assert outsider.get(POSITIONS, headers={
            "Cf-Access-Authenticated-User-Email": LEAD_EMAIL,
            "Cf-Access-Jwt-Assertion": "unverified-fixture",
        }).status_code == 401
        assert outsider.post("/api/auth/signup", json={}).status_code == 404
        assert outsider.post("/api/auth/register", json={}).status_code == 404
        with monkeypatch.context() as sandbox:
            sandbox.setenv("MIST_ACCOUNT_MODE", "sandbox")
            ok(sign_in(outsider, "director@example.com", os.environ["SEED_PASSWORD"]))
            assert outsider.get(POSITIONS).status_code == 403
        assert outsider.get("/api/auth/me").status_code == 401
        assert outsider.patch(f"/api/users/{ok(lead.get('/api/auth/me'))['id']}", json={
            "role": "competitions_member", "permissions": [], "is_active": False,
        }).status_code in {401, 403}

    with account_client() as member:
        head = invite_and_activate(lead, member)
        assert head["department"]["slug"] == "arts"
        assert member.get(POSITIONS).status_code == 403
        assert send_invite(member, "brackets", REPLACEMENT_EMAIL).status_code == 403
        assert member.patch("/api/auth/me", json={
            "full_name": "Fixture Head", "role": "competitions_chair", "department": "brackets",
        }).status_code == 422
        assert member.patch(f"/api/users/{head['id']}", json={
            "role": "competitions_chair", "permissions": [], "is_active": True,
        }).status_code == 403
        assert ok(member.get("/api/auth/me"))["department"]["slug"] == "arts"
        assert ok(member.get("/api/competitions", params={"category_slug": "group-projects", "event_year": 2027}))["items"]
        dashboard = ok(member.get(f"{ACCOUNTS}/department-dashboard", params={"event_year": 2027}))
        assert dashboard["items"]
        assert {item["category_slug"] for item in dashboard["items"]} == {"arts"}
        assert ok(member.get("/api/auth/me"))["department"]["slug"] == "arts"
        campaign = ok(member.post("/api/campaigns", json={
            "name": "Account permission regression",
            "subject_template": "Sandbox invitation",
            "body_template": "Prepared for a synthetic fixture.",
            "recipients": [{"email": "judge-fixture@example.org"}],
        }))
        campaign_path = f"/api/campaigns/{campaign['id']}"
        ok(member.post(f"{campaign_path}/submit"))
        assert member.post(f"{campaign_path}/approve").status_code == 403
        assert ok(lead.post(f"{campaign_path}/approve"))["status"] == "approved"


def test_invitation_is_bound_to_address_and_position_and_can_only_be_used_once(lead):
    assert send_invite(lead, email="fixture.external@example.org").status_code == 422
    assert send_invite(lead, email="fixture.external@getmistified.com.example.org").status_code == 422
    created = send_invite(lead, email=f"  {HEAD_EMAIL.upper()}  ")
    ok(created)
    current = position(lead, "arts")
    assert current["state"] == "invited"
    assert current["invitation_status"] == "pending"
    assert current["email"] == HEAD_EMAIL
    assert send_invite(lead, "brackets").status_code == 409
    assert send_invite(lead, email=REPLACEMENT_EMAIL).status_code == 409
    assert send_invite(lead, "brackets", LEAD_EMAIL).status_code == 409

    challenge_id, token = last_challenge(HEAD_EMAIL)
    assert token not in created.text and token not in lead.get(POSITIONS).text
    assert token not in lead.get(f"{POSITIONS}/arts/history").text
    with SessionLocal() as db:
        challenge = db.get(AccountToken, challenge_id)
        assert challenge.token_hash == hashlib.sha256(token.encode()).hexdigest()
        assert str(challenge.assignment_id) == current["assignment_id"]
        queued = db.scalar(select(Outbound).where(Outbound.account_token_id == challenge_id))
        assert queued.recipient_email == HEAD_EMAIL
        assert token not in queued.body_text

    with account_client() as invitee:
        assert sign_in(invitee, HEAD_EMAIL).status_code == 401
        assert activate(invitee, token, email=REPLACEMENT_EMAIL, department_slug="brackets",
                        role="competitions_chair").status_code == 422
        assert invitee.post(f"{ACCOUNTS}/activate", headers={"X-MIST-Request": "0"}, json={
            "token": token, "full_name": "Fixture Head", "password": PASSWORD,
        }).status_code == 403
        ok(activate(invitee, token))
        assert activate(invitee, token).status_code == 400
        login_response = sign_in(invitee, HEAD_EMAIL)
        head = ok(login_response)
        assert head["email"] == HEAD_EMAIL and head["role"] == "competitions_member"
        assert head["department"]["slug"] == "arts"
        cookie = login_response.headers["set-cookie"].lower()
        assert all(attribute in cookie for attribute in ("httponly", "secure", "samesite=strict"))
        assert sign_in(invitee, REPLACEMENT_EMAIL).status_code == 401
    assert position(lead, "arts")["state"] == "active"
    assert send_invite(lead, "brackets").status_code == 409
    with SessionLocal() as db:
        account = db.get(User, UUID(head["id"]))
        assert account.password_hash != PASSWORD
        assert hasher.verify(account.password_hash, PASSWORD)


def test_sports_dashboard_prioritizes_each_division_while_preserving_shared_access(lead):
    dashboard_path = f"{ACCOUNTS}/department-dashboard?event_year=2027"
    with account_client() as brothers, account_client() as sisters:
        assert invite_and_activate(lead, brothers, "brothers-sports")["department"]["slug"] == "brothers-sports"
        assert invite_and_activate(lead, sisters, "sisters-sports", REPLACEMENT_EMAIL)["department"]["slug"] == "sisters-sports"
        brothers_events = {item["name"] for item in ok(brothers.get(dashboard_path))["items"]}
        sisters_events = {item["name"] for item in ok(sisters.get(dashboard_path))["items"]}
        assert brothers_events == {"Brother's Basketball", "Brother's Soccer"}
        assert sisters_events == {"Sister's Basketball", "Sister's Soccer"}
        for client in (brothers, sisters):
            competitions = ok(client.get("/api/competitions", params={
                "category_slug": "sports-referees", "event_year": 2027,
            }))
            assert {item["name"] for item in competitions["items"]} >= brothers_events | sisters_events


def test_resending_revoking_and_expiring_invitations(lead):
    ok(send_invite(lead))
    assignment_id = position(lead, "arts")["assignment_id"]
    first_id, first = last_challenge(HEAD_EMAIL)
    ok(position_action(lead, "arts", "resend", assignment_id))
    second_id, second = last_challenge(HEAD_EMAIL)
    assert first != second
    with account_client() as invitee:
        assert activate(invitee, first).status_code == 400
        with SessionLocal.begin() as db:
            assert db.get(AccountToken, first_id).revoked_at
            db.get(AccountToken, second_id).expires_at = now() - timedelta(seconds=1)
        assert activate(invitee, second).status_code == 400
        history = ok(lead.get(f"{POSITIONS}/arts/history"))["items"]
        assert any("expir" in row["action"] for row in history)
        ok(position_action(lead, "arts", "resend", assignment_id))
        current_id, current = last_challenge(HEAD_EMAIL)
        assert current not in {first, second}
        ok(position_action(lead, "arts", "revoke-invitation", assignment_id))
        assert activate(invitee, current).status_code == 400
        assert position(lead, "arts")["state"] == "revoked"
        with SessionLocal() as db:
            assert db.get(AccountToken, current_id).revoked_at
    ok(send_invite(lead, email=REPLACEMENT_EMAIL))
    assert position_action(lead, "arts", "revoke-invitation", assignment_id).status_code == 409
    assert position(lead, "arts")["email"] == REPLACEMENT_EMAIL


def test_concurrent_activation_creates_one_account_and_consumes_one_token(lead):
    ok(send_invite(lead))
    _, token = last_challenge(HEAD_EMAIL)
    barrier = Barrier(2)

    def attempt():
        with account_client() as client:
            barrier.wait(timeout=10)
            return activate(client, token).status_code

    with ThreadPoolExecutor(max_workers=2) as pool:
        requests = [pool.submit(attempt) for _ in range(2)]
        assert sorted(request.result(timeout=15) for request in requests) == [200, 400]
    with SessionLocal() as db:
        count = db.scalar(select(func.count()).select_from(User).where(User.email == HEAD_EMAIL))
        assert count == 1


def test_private_delivery_only_renders_the_current_invitation_in_memory(lead, monkeypatch):
    ok(send_invite(lead))
    assignment_id = position(lead, "arts")["assignment_id"]
    _, old_token = last_challenge(HEAD_EMAIL)
    ok(position_action(lead, "arts", "resend", assignment_id))
    token_id, current_token = last_challenge(HEAD_EMAIL)
    delivered = []
    send = MockEmailProvider.send

    def capture(provider, message, *, body_text=None):
        delivered.append((message.recipient_email, body_text))
        return send(provider, message, body_text=body_text)

    monkeypatch.setattr(MockEmailProvider, "send", capture)
    for _ in range(5):
        if not run_once("test-private-account-mail"):
            break
    assert len(delivered) == 1
    assert delivered[0][0] == HEAD_EMAIL
    assert f"{ORIGIN}/activate#token={current_token}" in delivered[0][1]
    assert old_token not in delivered[0][1]

    with SessionLocal() as db:
        message = db.scalar(select(Outbound).where(Outbound.account_token_id == token_id))
        assert message.status == "sent"
        assert current_token not in message.body_text
        message_id = message.id
        for event in db.scalars(select(Audit)):
            assert current_token not in json.dumps([event.before_data, event.after_data, event.summary])
    outbox = ok(lead.get("/api/outbox", params={"q": HEAD_EMAIL}))
    assert outbox["total"] == 0
    assert lead.post(f"/api/outbox/{message_id}/forward", json={
        "recipient_email": REPLACEMENT_EMAIL,
    }).status_code == 400


def test_lead_can_move_deactivate_and_replace_a_head_without_reviving_sessions(lead):
    with account_client() as head_client, account_client() as other_session:
        head = invite_and_activate(lead, head_client)
        first_assignment = position(lead, "arts")["assignment_id"]
        ok(position_action(lead, "arts", "reassign", first_assignment, target_slug="group-projects"))
        current = position(lead, "group-projects")
        assert current["email"] == HEAD_EMAIL
        assert current["assignment_id"] != first_assignment
        if head_client.get("/api/auth/me").status_code == 401:
            ok(sign_in(head_client, HEAD_EMAIL))
        assert ok(head_client.get("/api/auth/me"))["department"]["slug"] == "group-projects"
        ok(sign_in(other_session, HEAD_EMAIL))
        ok(position_action(lead, "group-projects", "deactivate", current["assignment_id"]))
        assert position(lead, "group-projects")["state"] == "deactivated"
        assert head_client.get("/api/auth/me").status_code == 401
        assert other_session.get("/api/competitions").status_code == 401
        assert sign_in(head_client, HEAD_EMAIL).status_code == 401
        assert send_invite(lead, "group-projects", REPLACEMENT_EMAIL).status_code == 409

        ok(position_action(lead, "group-projects", "reactivate", current["assignment_id"]))
        assert other_session.get("/api/auth/me").status_code == 401
        ok(sign_in(head_client, HEAD_EMAIL))
        ok(position_action(lead, "group-projects", "revoke-account", current["assignment_id"]))
        assert head_client.get("/api/auth/me").status_code == 401
        assert position_action(lead, "group-projects", "reactivate", current["assignment_id"]).status_code == 409
        assert send_invite(lead, "group-projects", HEAD_EMAIL.upper()).status_code == 409
        replacement = invite_and_activate(lead, other_session, "group-projects", REPLACEMENT_EMAIL)
        assert replacement["id"] != head["id"]
        assert replacement["department"]["slug"] == "group-projects"

    with SessionLocal() as db:
        history = db.scalars(select(DepartmentAssignment).where(
            DepartmentAssignment.email == HEAD_EMAIL,
        ).order_by(DepartmentAssignment.created_at)).all()
        assert len(history) == 2 and all(row.ended_at for row in history)
        assert [row.department_slug for row in history] == ["arts", "group-projects"]
        assert db.get(User, UUID(head["id"])).is_active is False
        assert not db.scalars(select(Session).where(Session.user_id == UUID(head["id"]))).all()


def test_reset_does_not_enumerate_accounts_and_revokes_existing_sessions(lead):
    with account_client() as account, account_client() as public:
        invite_and_activate(lead, account)
        def request(email):
            return public.post(RESET_REQUEST, json={
                "email": email, "kind": "email", "destination": backup_email(HEAD_EMAIL),
            })
        unknown = ok(request("fixture.unknown@getmistified.com"))
        assert ok(request("fixture.external@example.org")) == unknown
        assert ok(request(HEAD_EMAIL)) == unknown
        with SessionLocal() as db:
            assert set(db.scalars(select(AccountResetRequest.email))) == {"fixture.unknown@getmistified.com", HEAD_EMAIL}
            assert db.scalar(select(AccountToken.id).where(AccountToken.purpose == "reset")) is None
        assert run_once("test-reset-request")
        with SessionLocal() as db:
            assert db.scalar(select(AccountResetRequest.id)) is None
            token = db.scalar(select(AccountToken).where(AccountToken.purpose == "reset"))
            assert db.get(User, token.user_id).email == HEAD_EMAIL
            assert token.recovery_method_id is not None
            assert token.email not in {HEAD_EMAIL, backup_email(HEAD_EMAIL)}
        token_id, token = last_challenge(HEAD_EMAIL, "reset")
        assert ok(request(HEAD_EMAIL.upper())) == unknown
        assert run_once("test-reset-request")
        assert last_challenge(HEAD_EMAIL, "reset")[0] == token_id
        assert unknown == {"ok": True}
        ok(public.post(RESET_COMPLETE, json={"token": token, "password": NEW_PASSWORD}))
        assert public.post(RESET_COMPLETE, json={"token": token, "password": PASSWORD}).status_code == 400
        assert account.get("/api/auth/me").status_code == 401
        assert sign_in(account, HEAD_EMAIL).status_code == 401
        ok(sign_in(account, HEAD_EMAIL, NEW_PASSWORD))


def test_expired_password_reset_does_not_change_password(lead):
    with account_client() as account, account_client() as public:
        invite_and_activate(lead, account)
        ok(public.post(RESET_REQUEST, json={
            "email": HEAD_EMAIL, "kind": "email", "destination": backup_email(HEAD_EMAIL),
        }))
        assert run_once("test-reset-expiration")
        token_id, token = last_challenge(HEAD_EMAIL, "reset")
        with SessionLocal.begin() as db:
            db.get(AccountToken, token_id).expires_at = now() - timedelta(seconds=1)
        assert public.post(RESET_COMPLETE, json={"token": token, "password": NEW_PASSWORD}).status_code == 400
        assert account.get("/api/auth/me").status_code == 200
        assert sign_in(account, HEAD_EMAIL, NEW_PASSWORD).status_code == 401
        ok(sign_in(account, HEAD_EMAIL))
