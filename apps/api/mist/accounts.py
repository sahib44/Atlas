"""Invitation-only identity and department lifecycle. All mail uses the sandbox queue."""

import argparse
import base64
import hashlib
import hmac
import os
import sys
from datetime import timedelta
from getpass import getpass
from typing import Literal
from urllib.parse import urlsplit
from uuid import UUID, uuid4

from argon2.exceptions import VerifyMismatchError
from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import Field
from sqlalchemy import String, cast, delete, func, select
from sqlalchemy.dialects.postgresql import insert

from .account_config import E2E_GROUP_PROJECTS_HEAD_EMAIL, e2e_demo_mode, managed_mode, managed_origin, recovery_key, signing_key
from .auth import current_user, fail, hasher
from .db import SessionLocal, get_db, now
from .models import AccountResetRequest, AccountToken, Audit, Department, DepartmentAssignment, Outbound, PilotCompetition, RecoveryMethod, Session, User
from .schemas import Input
from .services import audit, contact_email, contact_name

router = APIRouter(prefix="/api/accounts")
DEPARTMENTS = (
    ("arts", "Arts", "arts"),
    ("knowledge-quran", "Knowledge & Quran", "knowledge-quran"),
    ("writing-oratory", "Writing & Oratory", "writing-oratory"),
    ("brackets", "Brackets", "brackets"),
    ("group-projects", "Group Projects", "group-projects"),
    ("brothers-sports", "Brothers Sports", "sports-referees"),
    ("sisters-sports", "Sisters Sports", "sports-referees"),
)
INVITE_LIFETIME = timedelta(hours=48)
RESET_LIFETIME = timedelta(minutes=30)
RESET_COOLDOWN = timedelta(minutes=5)
INVALID_TOKEN = "This link is invalid or no longer available. Ask for a new link."
PRIVATE_RECOVERY_ADDRESS = "undisclosed@recovery.invalid"


class EmailInput(Input):
    email: str = Field(max_length=254)


class ResetRequestInput(EmailInput):
    kind: Literal["email", "sms"] = "email"
    destination: str = Field(default="", max_length=254)


class AssignmentInput(Input):
    assignment_id: UUID


class ReassignmentInput(AssignmentInput):
    target_slug: str = Field(max_length=80)


class TokenInput(Input):
    token: str = Field(max_length=256)
    password: str = Field(min_length=12, max_length=200)


class ActivationInput(TokenInput):
    full_name: str = Field(min_length=1, max_length=200)


def lock_accounts(db):
    db.execute(select(func.pg_advisory_xact_lock(410263)))


def require_managed_lead(user=Depends(current_user)):
    if not managed_mode() or not user.managed_account or user.role != "competitions_chair":
        fail("forbidden", "Only the Competitions Lead can manage competition head accounts.", 403)
    return user


def public_account_request(request: Request):
    if not managed_mode():
        fail("not_found", "Account activation is unavailable.", 404)
    if request.headers.get("X-MIST-Request") != "1":
        fail("csrf", "Missing request protection header.", 403)


def mist_email(value):
    try:
        address = contact_email(value)
    except HTTPException:
        fail("invalid_email", "Enter a valid individual @getmistified.com address.", 422)
    if address.partition("@")[2] != "getmistified.com":
        fail("invalid_email", "Enter a valid individual @getmistified.com address.", 422)
    return address


def validated_name(value):
    name = contact_name(value)
    if not name or len(name) > 200:
        fail("invalid_name", "Enter a display name of up to 200 characters.", 422)
    return name


def find_position(db, slug):
    position = db.get(Department, slug)
    if not position:
        fail("not_found", "Competition head position not found.", 404)
    return position


def live_assignment(db, **criteria):
    return db.scalar(select(DepartmentAssignment).filter_by(ended_at=None, **criteria))


def department_for(db, user):
    if user.role != "competitions_member":
        return None
    if not user.managed_account:
        if user.email == E2E_GROUP_PROJECTS_HEAD_EMAIL and e2e_demo_mode():
            slug, name, category_slug = next(item for item in DEPARTMENTS if item[0] == "group-projects")
            return {"slug": slug, "name": name, "category_slug": category_slug}
        return None
    if not managed_mode():
        return None
    assignment = live_assignment(db, user_id=user.id, email=user.email)
    if assignment is None:
        return None
    department = db.get(Department, assignment.department_slug)
    return {"slug": department.slug, "name": department.name, "category_slug": department.category_slug}


def latest_assignment(db, slug):
    return db.scalar(
        select(DepartmentAssignment).where(DepartmentAssignment.department_slug == slug)
        .order_by(DepartmentAssignment.ended_at.desc().nulls_first(), DepartmentAssignment.created_at.desc(), DepartmentAssignment.id.desc())
        .limit(1)
    )


def current_assignment(db, slug, assignment_id):
    assignment = live_assignment(db, department_slug=slug)
    if not assignment or assignment.id != assignment_id:
        fail("stale_assignment", "This position changed. Refresh the list and try again.", 409)
    return assignment


def account_on_record(db, address):
    return db.scalar(select(User.id).where(func.lower(User.email) == address).limit(1)) is not None


def assignment_audit(db, actor, assignment, action, summary, **details):
    audit(db, actor, f"account.{action}", assignment, summary, after={
        "department_slug": assignment.department_slug,
        "email": assignment.email,
        **details,
    })


def open_tokens():
    return select(AccountToken).where(
        AccountToken.used_at.is_(None), AccountToken.revoked_at.is_(None), AccountToken.expired_at.is_(None)
    )


def token_is_open(token):
    return token.used_at is None and token.revoked_at is None and token.expired_at is None


def token_audit(db, actor, token, action, summary):
    if token.purpose == "activate":
        assignment = db.get(DepartmentAssignment, token.assignment_id)
        assignment_audit(db, actor, assignment, f"invitation_{action}", summary)
        return
    event = "recovery_verification" if token.purpose == "verify_recovery" else "password_reset"
    audit(db, actor, f"account.{event}_{action}", token, summary)


def mark_expired(db, token, stamp):
    token.expired_at = stamp
    token_audit(db, None, token, "expired", "Account link expired")


def expire_account_tokens(db):
    """Close and audit due tokens. The caller owns the transaction."""
    if not managed_mode():
        return 0
    lock_accounts(db)
    stamp = now()
    expired = list(db.scalars(open_tokens().where(AccountToken.expires_at <= stamp)))
    for token in expired:
        mark_expired(db, token, stamp)
    return len(expired)


def invalidate_tokens(db, actor=None, **scope):
    stamp = now()
    for token in db.scalars(open_tokens().filter_by(**scope)):
        if token.expires_at <= stamp:
            mark_expired(db, token, stamp)
        else:
            token.revoked_at = stamp
            token_audit(db, actor, token, "revoked", "Previous account link revoked")
    db.flush()


def raw_account_token(token):
    """Derive the bearer only in memory from a random nonce and a separate key."""
    payload = b"mist-atlas-account\0" + token.purpose.encode("ascii") + b"\0" + token.id.bytes
    signature = hmac.digest(signing_key(), payload, "sha256")
    return base64.urlsafe_b64encode(signature).rstrip(b"=").decode("ascii")


def token_digest(value):
    return hashlib.sha256(value.encode()).hexdigest()


def issue_token(db, actor, purpose, address=None, assignment=None, user=None, resent=False, recovery_method=None):
    address = PRIVATE_RECOVERY_ADDRESS if recovery_method is not None else address
    token = AccountToken(
        id=uuid4(), purpose=purpose, email=address,
        assignment_id=assignment.id if assignment else None,
        user_id=user.id if user else None,
        recovery_method_id=recovery_method.id if recovery_method else None,
        expires_at=now() + (INVITE_LIFETIME if purpose == "activate" else RESET_LIFETIME),
    )
    token.token_hash = token_digest(raw_account_token(token))
    db.add(token)
    db.flush()
    subjects = {
        "activate": "Activate your MIST Atlas account",
        "reset": "Reset your MIST Atlas password",
        "verify_recovery": "Verify your MIST Atlas recovery method",
    }
    db.add(Outbound(
        account_token_id=token.id, recipient_email=address, recipient_name=address,
        subject=subjects[purpose],
        provider="sms" if recovery_method is not None and recovery_method.kind == "sms" else "mock",
        body_text="Private account message. Its link is generated in memory during delivery.",
        idempotency_key=f"account:{token.id}",
    ))
    events = {
        "activate": ("Invitation resent", "Invitation created"),
        "verify_recovery": ("Recovery verification resent", "Recovery verification created"),
        "reset": ("Password reset resent", "Password reset requested"),
    }
    token_audit(db, actor, token, "resent" if resent else "created", events[purpose][0 if resent else 1])
    return token


def token_context(db, token):
    assignment = db.get(DepartmentAssignment, token.assignment_id) if token.assignment_id else None
    if token.purpose == "activate":
        if not assignment or assignment.ended_at is not None or assignment.user_id or assignment.email != token.email:
            return None
        return assignment, None
    user = db.get(User, token.user_id)
    if not user or not user.is_active or not user.managed_account or not token.recovery_method_id:
        return None
    if user.role == "competitions_member":
        assignment = live_assignment(db, user_id=user.id, email=user.email)
        if assignment is None or (token.assignment_id and token.assignment_id != assignment.id):
            return None
    elif user.role != "competitions_chair" or assignment:
        return None
    method = db.get(RecoveryMethod, token.recovery_method_id)
    if not method or method.user_id != user.id or method.removed_at is not None:
        return None
    from .recovery import uses_shared_mailbox

    if uses_shared_mailbox(user, method):
        return None
    if (token.purpose == "reset") != bool(method.verified_at):
        return None
    return assignment, user


def account_mail_delivery(db, msg):
    """Resolve a private message and destination in memory for the worker."""
    if not managed_mode() or msg.account_token_id is None:
        return None
    lock_accounts(db)
    token = db.get(AccountToken, msg.account_token_id)
    if (
        not token or not token_is_open(token) or token.expires_at <= now()
        or msg.recipient_email != token.email or token_context(db, token) is None
    ):
        return None
    raw = raw_account_token(token)
    if not hmac.compare_digest(token.token_hash, token_digest(raw)):
        return None
    routes = {"activate": "/activate", "reset": "/reset-password", "verify_recovery": "/verify-recovery"}
    route = routes[token.purpose]
    body = (
        f"{msg.subject}\n\n{managed_origin()}{route}#token={raw}\n\n"
        "This link can be used once. If you did not expect this message, you can ignore it."
    )
    if token.recovery_method_id:
        from .recovery import decrypt_destination

        method = db.get(RecoveryMethod, token.recovery_method_id)
        return method.kind, decrypt_destination(method), body
    return "email", token.email, body


def account_mail_payload(db, msg):
    delivery = account_mail_delivery(db, msg)
    return delivery[2] if delivery else None


def usable_token(db, purpose, raw, *, owner_id=None):
    lock_accounts(db)
    token = db.scalar(select(AccountToken).where(AccountToken.token_hash == token_digest(raw), AccountToken.purpose == purpose))
    if token and (owner_id is None or token.user_id == owner_id) and token_is_open(token):
        if token.expires_at <= now():
            mark_expired(db, token, now())
            db.commit()
        elif hmac.compare_digest(raw_account_token(token).encode("ascii"), raw.encode()):
            context = token_context(db, token)
            if context is not None:
                return token, *context
    fail("invalid_token", INVALID_TOKEN)


def position_view(db, position):
    row = {
        "slug": position.slug, "name": position.name, "category_slug": position.category_slug,
        "state": "unassigned", "email": None, "assignment_id": None, "account_id": None,
        "invitation_status": "none", "delivery_status": None, "expires_at": None,
    }
    assignment = latest_assignment(db, position.slug)
    if not assignment or assignment.end_reason == "reassigned":
        return row
    row.update(email=assignment.email, assignment_id=assignment.id, account_id=assignment.user_id)
    invitation = db.scalar(
        select(AccountToken).where(AccountToken.assignment_id == assignment.id, AccountToken.purpose == "activate")
        .order_by(AccountToken.created_at.desc(), AccountToken.id.desc()).limit(1)
    )
    if invitation:
        queued = db.scalar(select(Outbound).where(Outbound.account_token_id == invitation.id))
        row.update(expires_at=invitation.expires_at, delivery_status=queued.status if queued else None)
    if assignment.ended_at:
        row.update(state="revoked", invitation_status="revoked")
    elif assignment.user_id:
        user = db.get(User, assignment.user_id)
        row.update(state="active" if user.is_active else "deactivated", invitation_status="accepted")
    else:
        status = "expired" if invitation and (invitation.expired_at or invitation.expires_at <= now()) else "pending"
        row.update(state="invited", invitation_status=status)
    return row


@router.get("/positions")
def positions(user=Depends(require_managed_lead), db=Depends(get_db)):
    return {"items": [position_view(db, row) for row in db.scalars(select(Department).order_by(Department.display_order, Department.slug))]}


@router.get("/positions/{slug}/history")
def position_history(slug: str, user=Depends(require_managed_lead), db=Depends(get_db)):
    find_position(db, slug)
    assignments = select(cast(DepartmentAssignment.id, String)).where(DepartmentAssignment.department_slug == slug)
    events = db.scalars(
        select(Audit).where(Audit.entity_type == "department_assignments", Audit.entity_id.in_(assignments))
        .order_by(Audit.created_at.desc(), Audit.id.desc()).limit(100)
    )
    return {"items": [
        {"id": event.id, "action": event.action,
         "summary": event.summary.replace("Department ", "Competition head ", 1) if event.summary.startswith("Department ") else event.summary,
         "created_at": event.created_at,
         "email": (event.after_data or {}).get("email")}
        for event in events
    ]}


@router.post("/positions/{slug}/invite")
def invite(slug: str, data: EmailInput, user=Depends(require_managed_lead), db=Depends(get_db)):
    address = mist_email(data.email)
    lock_accounts(db)
    position = find_position(db, slug)
    if live_assignment(db, department_slug=slug):
        fail("position_occupied", "This position already has an account or invitation.", 409)
    if address == configured_lead_email() or account_on_record(db, address) or live_assignment(db, email=address):
        fail("email_unavailable", "This address is unavailable for a competition head invitation.", 409)
    assignment = DepartmentAssignment(department_slug=slug, email=address)
    db.add(assignment)
    db.flush()
    issue_token(db, user, "activate", address, assignment=assignment)
    result = position_view(db, position)
    db.commit()
    return result


@router.post("/positions/{slug}/resend")
def resend(slug: str, data: AssignmentInput, user=Depends(require_managed_lead), db=Depends(get_db)):
    lock_accounts(db)
    position = find_position(db, slug)
    assignment = current_assignment(db, slug, data.assignment_id)
    if assignment.user_id:
        fail("wrong_account_state", "This position already has an activated account.", 409)
    invalidate_tokens(db, user, assignment_id=assignment.id)
    issue_token(db, user, "activate", assignment.email, assignment=assignment, resent=True)
    result = position_view(db, position)
    db.commit()
    return result


@router.post("/positions/{slug}/revoke-invitation")
def revoke_invitation(slug: str, data: AssignmentInput, user=Depends(require_managed_lead), db=Depends(get_db)):
    lock_accounts(db)
    position = find_position(db, slug)
    assignment = current_assignment(db, slug, data.assignment_id)
    if assignment.user_id:
        fail("wrong_account_state", "This position already has an activated account.", 409)
    invalidate_tokens(db, user, assignment_id=assignment.id)
    assignment.ended_at, assignment.end_reason = now(), "revoked"
    assignment_audit(db, user, assignment, "position_freed", "Invitation withdrawn and position made available")
    result = position_view(db, position)
    db.commit()
    return result


def assigned_member(db, assignment):
    account = db.get(User, assignment.user_id) if assignment.user_id else None
    if not account or not account.managed_account or account.role != "competitions_member":
        fail("wrong_account_state", "This position has no activated competition head account.", 409)
    return account


def invalidate_user_sessions(db, account):
    db.execute(delete(Session).where(Session.user_id == account.id))


@router.post("/positions/{slug}/deactivate")
@router.post("/positions/{slug}/reactivate")
@router.post("/positions/{slug}/revoke-account")
def change_account_status(slug: str, data: AssignmentInput, request: Request, user=Depends(require_managed_lead), db=Depends(get_db)):
    action = request.url.path.rsplit("/", 1)[-1]
    lock_accounts(db)
    position = find_position(db, slug)
    assignment = current_assignment(db, slug, data.assignment_id)
    account = assigned_member(db, assignment)
    if (action == "deactivate" and not account.is_active) or (action == "reactivate" and account.is_active):
        fail("wrong_account_state", "This account is already in that state.", 409)
    account.is_active = action == "reactivate"
    if action == "revoke-account":
        assignment.ended_at, assignment.end_reason = now(), "revoked"
    invalidate_tokens(db, user, user_id=account.id)
    invalidate_user_sessions(db, account)
    events = {
        "deactivate": ("account_deactivated", "Competition head account deactivated"),
        "reactivate": ("account_reactivated", "Competition head account reactivated"),
        "revoke-account": ("account_revoked", "Competition head account revoked and position made available"),
    }
    event, message = events[action]
    assignment_audit(db, user, assignment, event, message)
    result = position_view(db, position)
    db.commit()
    return result


@router.post("/positions/{slug}/reassign")
def reassign(slug: str, data: ReassignmentInput, user=Depends(require_managed_lead), db=Depends(get_db)):
    lock_accounts(db)
    source = find_position(db, slug)
    target = find_position(db, data.target_slug)
    assignment = current_assignment(db, source.slug, data.assignment_id)
    account = assigned_member(db, assignment)
    if not account.is_active:
        fail("wrong_account_state", "Reactivate this account before moving it.", 409)
    if live_assignment(db, department_slug=target.slug):
        fail("position_occupied", "Choose an unassigned position.", 409)
    invalidate_tokens(db, user, user_id=account.id)
    assignment.ended_at, assignment.end_reason = now(), "reassigned"
    assignment_audit(db, user, assignment, "reassigned_from", "Competition head account moved to another position", target_slug=target.slug)
    replacement = DepartmentAssignment(department_slug=target.slug, email=account.email, user_id=account.id)
    db.add(replacement)
    assignment_audit(db, user, replacement, "reassigned_to", "Competition head account assigned from another position", source_slug=source.slug)
    result = position_view(db, target)
    db.commit()
    return result


@router.post("/activate", dependencies=[Depends(public_account_request)])
def activate(data: ActivationInput, db=Depends(get_db)):
    name = validated_name(data.full_name)
    token, assignment, _ = usable_token(db, "activate", data.token)
    if account_on_record(db, assignment.email):
        fail("invalid_token", INVALID_TOKEN)
    account = User(
        email=assignment.email, role="competitions_member", managed_account=True, permissions=[],
        full_name=name, password_hash=hasher.hash(data.password), is_active=True,
    )
    db.add(account)
    db.flush()
    assignment.user_id = account.id
    token.used_at = now()
    token_audit(db, account, token, "accepted", "Competition head invitation accepted")
    db.commit()
    return {"ok": True, "email": account.email}


@router.post("/password-reset/request", dependencies=[Depends(public_account_request)])
def request_reset(data: ResetRequestInput, db=Depends(get_db)):
    from .recovery import check_public_reset_attempt, destination_key, normalize_destination, sms_available

    try:
        address = mist_email(data.email)
        destination = normalize_destination(data.kind, data.destination)
    except HTTPException:
        return {"ok": True}
    if data.kind == "sms" and not sms_available():
        return {"ok": True}
    key = destination_key(address, data.kind, destination)
    if not check_public_reset_attempt(address, key):
        return {"ok": True}
    # Submissions share a capacity limit and do the same work for all MIST
    # addresses. Only the worker resolves identity against the accounts table.
    db.execute(select(func.pg_advisory_xact_lock(410264)))
    db.execute(delete(AccountResetRequest).where(AccountResetRequest.created_at <= now() - RESET_LIFETIME))
    if db.scalar(select(func.count()).select_from(AccountResetRequest)) >= 256:
        fail("reset_queue_busy", "Password recovery is busy. Try again shortly.", 503)
    db.add(AccountResetRequest(email=address, recovery_kind=data.kind, recovery_key=key))
    db.commit()
    return {"ok": True}


def process_reset_requests(db):
    from .recovery import clear_expired_rates

    lock_accounts(db)
    clear_expired_rates(db)
    requests = list(db.scalars(select(AccountResetRequest).order_by(
        AccountResetRequest.created_at, AccountResetRequest.id,
    ).limit(16).with_for_update()))
    oldest = now() - RESET_LIFETIME
    for request in requests:
        if request.created_at > oldest and request.recovery_key:
            queue_reset_email(db, request.email, request.recovery_kind, request.recovery_key)
        db.delete(request)
    return len(requests)


def queue_reset_email(db, address, kind, key):
    from .recovery import sms_available, verified_methods

    user = db.scalar(select(User).where(User.email == address, User.managed_account, User.is_active))
    if not user or (kind == "sms" and not sms_available()):
        return
    assignment = live_assignment(db, user_id=user.id, email=address)
    if user.role == "competitions_member" and assignment is None:
        return
    method = db.scalar(verified_methods(user).where(RecoveryMethod.kind == kind, RecoveryMethod.destination_key == key))
    if not method:
        return
    recent = db.scalar(select(AccountToken.id).where(
        AccountToken.user_id == user.id, AccountToken.purpose == "reset",
        AccountToken.created_at > now() - RESET_COOLDOWN,
    ).limit(1))
    if recent:
        return
    invalidate_tokens(db, user_id=user.id, purpose="reset")
    issue_token(db, None, "reset", assignment=assignment, user=user, recovery_method=method)


@router.post("/password-reset/complete", dependencies=[Depends(public_account_request)])
def complete_reset(data: TokenInput, db=Depends(get_db)):
    from .recovery import check_reset_verification

    check_reset_verification(db, data.token)
    token, _, user = usable_token(db, "reset", data.token)
    user.password_hash = hasher.hash(data.password)
    token.used_at = now()
    invalidate_tokens(db, user, user_id=user.id, purpose="reset")
    invalidate_user_sessions(db, user)
    token_audit(db, user, token, "completed", "Account password reset and sessions revoked")
    db.commit()
    return {"ok": True}


def configured_lead_email():
    configured = os.getenv("MIST_COMPETITIONS_LEAD_EMAIL", "").strip()
    return mist_email(configured) if configured else None


def checked_lead_password(password):
    password = password.strip()
    if not 12 <= len(password) <= 200:
        fail("invalid_password", "Use a password of 12 to 200 characters.", 422)
    return password


def bootstrap_lead(db, email, full_name, password):
    if not managed_mode():
        fail("managed_mode_required", "Bootstrap requires MIST_ACCOUNT_MODE=managed.")
    managed_origin()
    signing_key()
    recovery_key()
    configured = configured_lead_email()
    address = mist_email(email) if email else configured
    if not address or (configured and configured != address):
        fail("invalid_lead_email", "Use the configured shared Competitions mailbox.", 422)
    name = validated_name(full_name)
    password = checked_lead_password(password)
    lock_accounts(db)
    if db.scalar(select(User.id).where(User.managed_account, User.role == "competitions_chair").limit(1)):
        fail("lead_exists", "A Competitions Lead has already been provisioned.", 409)
    if account_on_record(db, address) or live_assignment(db, email=address):
        fail("email_unavailable", "This address is already on record.", 409)
    user = User(
        email=address, full_name=name, role="competitions_chair",
        managed_account=True, password_hash=hasher.hash(password), permissions=[], is_active=True,
    )
    db.add(user)
    db.execute(insert(Department).values([
        {"slug": slug, "name": department, "category_slug": category, "display_order": index}
        for index, (slug, department, category) in enumerate(DEPARTMENTS)
    ]).on_conflict_do_nothing(index_elements=[Department.slug]))
    if db.scalar(select(PilotCompetition.id).limit(1)) is None:
        from .seed import seed_permanent_competitions

        seed_permanent_competitions(db, user)
    audit(db, None, "account.lead_bootstrapped", user, "Competitions Lead provisioned with the local bootstrap")
    db.commit()
    return user


def transition_lead(db, password):
    """Rotate the sole Lead identity in place and require new recovery enrollment."""
    from .recovery import clear_recovery_methods

    if not managed_mode():
        fail("managed_mode_required", "Lead transition requires MIST_ACCOUNT_MODE=managed.")
    managed_origin()
    signing_key()
    recovery_key()
    password = checked_lead_password(password)
    lock_accounts(db)
    user = db.scalar(select(User).where(User.managed_account, User.role == "competitions_chair", User.is_active))
    if not user:
        fail("lead_not_found", "No active Competitions Lead is available for transition.", 404)
    target = configured_lead_email() or user.email
    if target != user.email and (account_on_record(db, target) or live_assignment(db, email=target)):
        fail("email_unavailable", "The configured mailbox is already assigned.", 409)
    try:
        hasher.verify(user.password_hash, password)
    except VerifyMismatchError:
        pass
    else:
        fail("unchanged_lead_password", "Choose a new Atlas password for the incoming Lead.", 422)

    invalidate_user_sessions(db, user)
    invalidate_tokens(db, user_id=user.id)
    clear_recovery_methods(db, user)
    db.execute(delete(AccountResetRequest).where(AccountResetRequest.email.in_([user.email, target])))
    user.email = target
    user.password_hash = hasher.hash(password)
    audit(db, None, "account.lead_transition", user, "Competitions Lead access rotated and previous recovery methods removed")
    db.commit()
    return user


def cli():
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest="command", required=True)
    bootstrap = commands.add_parser("bootstrap", help="Provision the sole Competitions Lead with an interactive password.")
    bootstrap.add_argument("--email", help="Defaults to MIST_COMPETITIONS_LEAD_EMAIL.")
    bootstrap.add_argument("--name", required=True)
    commands.add_parser("transition-lead", help="Rotate the sole Lead login and clear old recovery methods.")
    sandbox = commands.add_parser("sandbox-link", help="Read one mock account link in an interactive local terminal.")
    sandbox.add_argument("--message-id", required=True, type=UUID)
    args = parser.parse_args()
    try:
        if args.command in {"bootstrap", "transition-lead"}:
            if not sys.stdin.isatty():
                parser.error("Run this command in an interactive terminal.")
            password = getpass("New Lead password: ")
            if password != getpass("Confirm password: "):
                parser.error("Passwords do not match.")
            with SessionLocal() as db:
                if args.command == "bootstrap":
                    bootstrap_lead(db, args.email, args.name, password)
                    print("Competitions Lead provisioned.")
                else:
                    transition_lead(db, password)
                    print("Lead transition completed. The incoming Lead must verify a recovery method.")
        else:
            local_host = urlsplit(managed_origin()).hostname in {"localhost", "127.0.0.1", "::1"}
            if os.getenv("MIST_ALLOW_TEST_ACCOUNT_LINKS") != "true" or not sys.stdout.isatty() or not local_host:
                parser.error("Mock account links require local opt-in and an interactive terminal.")
            with SessionLocal() as db:
                message = db.get(Outbound, args.message_id)
                payload = account_mail_payload(db, message) if message and message.provider == "mock" else None
                if not payload:
                    parser.error("No active mock account link for that message.")
                print(payload)
    except HTTPException as exc:
        parser.error(exc.detail["message"])
    except RuntimeError as exc:
        parser.error(str(exc))


if __name__ == "__main__":
    cli()
