"""Verified backup recovery methods for managed accounts."""

import hashlib
import hmac
import re
import secrets
from datetime import timedelta
from typing import Literal, Protocol
from uuid import UUID, uuid4

from argon2.exceptions import VerificationError
from cryptography.exceptions import InvalidTag
from cryptography.hazmat.primitives.ciphers.aead import AESGCM
from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import Field
from sqlalchemy import case, delete, or_, select
from sqlalchemy.dialects.postgresql import insert

from .account_config import recovery_key
from .accounts import (
    INVALID_TOKEN, configured_lead_email, invalidate_tokens, issue_token, lock_accounts, token_audit,
    token_digest, usable_token,
)
from .auth import fail, hasher, session_user
from .db import SessionLocal, get_db, now
from .models import AccountToken, RecoveryMethod, RecoveryRateLimit
from .schemas import Input
from .services import audit, contact_email

router = APIRouter(prefix="/api/accounts/recovery")
MAX_METHODS = 8
VERIFICATION_COOLDOWN = timedelta(minutes=1)


class SmsProvider(Protocol):
    def send(self, *, destination: str, body_text: str, idempotency_key: str) -> str: ...


def get_sms_provider() -> SmsProvider | None:
    return None


def sms_available():
    return get_sms_provider() is not None


class PasswordConfirmation(Input):
    password: str = Field(min_length=1, max_length=200)


class MethodInput(PasswordConfirmation):
    kind: Literal["email", "sms"]
    destination: str = Field(max_length=254)


class VerificationInput(Input):
    token: str = Field(max_length=256)


def keyed_digest(purpose, value):
    return hmac.digest(recovery_key(), purpose + b"\x00" + value.encode(), hashlib.sha256)


def mailbox_identity(address):
    local, _, domain = address.partition("@")
    return f"{local.partition('+')[0]}@{domain}" if domain == "getmistified.com" else address


def destination_key(login_address, kind, destination):
    if kind == "email":
        destination = mailbox_identity(destination)
    return keyed_digest(b"atlas-recovery-lookup", f"{login_address}\0{kind}\0{destination}").hex()


def associated_data(method):
    return method.user_id.bytes + method.id.bytes + method.kind.encode("ascii")


def encrypt_destination(method, destination):
    nonce = secrets.token_bytes(12)
    cipher = AESGCM(keyed_digest(b"atlas-recovery-encryption", "v1"))
    return nonce + cipher.encrypt(nonce, destination.encode(), associated_data(method))


def decrypt_destination(method):
    nonce, ciphertext = method.encrypted_destination[:12], method.encrypted_destination[12:]
    cipher = AESGCM(keyed_digest(b"atlas-recovery-encryption", "v1"))
    try:
        return cipher.decrypt(nonce, ciphertext, associated_data(method)).decode()
    except InvalidTag:
        fail("recovery_unavailable", "Recovery is temporarily unavailable.", 503)


def normalize_destination(kind, value):
    if kind == "email":
        return contact_email(value)
    number = re.sub(r"[\s().-]", "", value)
    if not re.fullmatch(r"\+[1-9][0-9]{7,14}", number):
        fail("invalid_recovery_phone", "Enter a phone number with its country code.", 422)
    return number


def active_methods(user):
    return select(RecoveryMethod).where(RecoveryMethod.user_id == user.id, RecoveryMethod.removed_at.is_(None))


def verified_methods(user):
    methods = active_methods(user).where(RecoveryMethod.verified_at.is_not(None))
    shared = configured_lead_email()
    if shared:
        methods = methods.where(or_(
            RecoveryMethod.kind != "email",
            RecoveryMethod.destination_key != destination_key(user.email, "email", shared),
        ))
    return methods


def has_verified_recovery(db, user):
    return db.scalar(verified_methods(user).with_only_columns(RecoveryMethod.id).limit(1)) is not None


def allow_mist_email(user):
    return user.role != "competitions_member"


def uses_shared_mailbox(user, method):
    shared = configured_lead_email()
    return bool(shared and method.kind == "email" and method.destination_key == destination_key(user.email, "email", shared))


def require_managed_account(user=Depends(session_user)):
    if not user.managed_account:
        fail("forbidden", "Recovery methods are available for managed accounts.", 403)
    return user


def limit_attempts(scope, subject, attempts, minutes):
    """Commit counters separately so invalid attempts and business rollbacks count."""
    key = keyed_digest(b"atlas-recovery-rate", f"{scope}\0{subject}").hex()
    stamp = now()
    expires = stamp + timedelta(minutes=minutes)
    bucket = RecoveryRateLimit
    expired = bucket.expires_at <= stamp
    stmt = insert(bucket).values(key=key, attempts=1, expires_at=expires)
    stmt = stmt.on_conflict_do_update(
        index_elements=[bucket.key],
        set_={
            "attempts": case((expired, 1), else_=bucket.attempts + 1),
            "expires_at": case((expired, expires), else_=bucket.expires_at),
        },
        where=or_(expired, bucket.attempts < attempts),
    ).returning(bucket.key)
    with SessionLocal.begin() as db:
        return db.scalar(stmt) is not None


def clear_expired_rates(db):
    db.execute(delete(RecoveryRateLimit).where(RecoveryRateLimit.expires_at <= now()))


def check_public_reset_attempt(login_address, key):
    return limit_attempts("reset-destination", f"{login_address}\0{key}", 6, 15)


def check_reset_verification(db, raw):
    # Avoid sharing a quota among every browser behind the same reverse proxy.
    # Unknown, random bearer values never get their own durable rate bucket.
    challenge = db.scalar(select(AccountToken.id).where(
        AccountToken.purpose == "reset", AccountToken.token_hash == token_digest(raw),
    ))
    db.rollback()
    if challenge and not limit_attempts("reset-verification", str(challenge), 6, 15):
        fail("recovery_rate_limited", "Too many reset attempts. Try again later.", 429)


def recovery_mutator(request: Request, db=Depends(get_db), user=Depends(require_managed_account)):
    user_id = user.id
    db.rollback()
    if not limit_attempts("method-change", str(user_id), 25, 15):
        fail("recovery_rate_limited", "Too many recovery attempts. Try again later.", 429)
    lock_accounts(db)
    return session_user(request, db)


def confirm_password(user, value):
    try:
        hasher.verify(user.password_hash, value)
    except VerificationError:
        fail("invalid_credentials", "Current password is incorrect.", 403)


def mask_destination(method):
    destination = decrypt_destination(method)
    if method.kind == "sms":
        return "•••• " + destination[-4:]
    local, domain = destination.split("@", 1)
    suffix = domain.rpartition(".")[2]
    return f"{local[:1]}•••@{domain[:1]}•••.{suffix}"


def method_view(method):
    return {
        "id": method.id,
        "kind": method.kind,
        "masked_destination": mask_destination(method),
        "verified": bool(method.verified_at),
        "preferred": method.preferred,
    }


def find_method(db, user, id):
    method = db.scalar(active_methods(user).where(RecoveryMethod.id == id))
    if method is None:
        fail("not_found", "Recovery method not found.", 404)
    return method


def clear_method(db, user, method):
    invalidate_tokens(db, user, recovery_method_id=method.id)
    method.removed_at = now()
    method.preferred = False
    method.encrypted_destination = None
    method.destination_key = None
    db.flush()
    audit(db, user, "account.recovery_method_removed", method, "Recovery method removed", after={"kind": method.kind})


def clear_recovery_methods(db, user):
    for method in list(db.scalars(active_methods(user))):
        clear_method(db, user, method)


def issue_verification(db, user, method, *, resent=False):
    issue_token(db, user, "verify_recovery", user=user, recovery_method=method, resent=resent)


def prefer_method(db, user, method):
    current = db.scalar(active_methods(user).where(RecoveryMethod.preferred))
    if current and current.id != method.id:
        current.preferred = False
        db.flush()
    method.preferred = True


@router.get("")
def methods(user=Depends(require_managed_account), db=Depends(get_db)):
    return {
        "methods": [method_view(item) for item in db.scalars(
            active_methods(user).order_by(RecoveryMethod.preferred.desc(), RecoveryMethod.created_at, RecoveryMethod.id),
        )],
        "allow_mist_email": allow_mist_email(user),
        "sms_available": sms_available(),
    }


@router.post("/methods")
def add_method(data: MethodInput, user=Depends(recovery_mutator), db=Depends(get_db)):
    confirm_password(user, data.password)
    if data.kind == "sms" and not sms_available():
        fail("sms_unavailable", "Phone recovery is unavailable until an SMS provider is configured.", 503)
    destination = normalize_destination(data.kind, data.destination)
    if data.kind == "email":
        address = mailbox_identity(destination)
        if address == mailbox_identity(user.email):
            fail("primary_recovery_email", "Use an email address different from your sign-in email.", 422)
        shared = configured_lead_email()
        if shared and address == mailbox_identity(shared):
            fail("shared_recovery_email", "Use a recovery address other than the shared Competitions mailbox.", 422)
        if destination.partition("@")[2] == "getmistified.com" and not allow_mist_email(user):
            fail("personal_recovery_email", "Use a personal email outside @getmistified.com.", 422)
    records = list(db.scalars(active_methods(user)))
    lookup = destination_key(user.email, data.kind, destination)
    if any(item.kind == data.kind and item.destination_key == lookup for item in records):
        fail("duplicate_recovery_method", "That recovery method is already registered.", 409)
    if len(records) >= MAX_METHODS:
        fail("recovery_limit", "Remove an existing method before adding another.", 409)
    method = RecoveryMethod(id=uuid4(), user_id=user.id, kind=data.kind, destination_key=lookup)
    method.encrypted_destination = encrypt_destination(method, destination)
    db.add(method)
    db.flush()
    issue_verification(db, user, method)
    audit(db, user, "account.recovery_method_added", method, "Recovery method added", after={"kind": method.kind})
    result = method_view(method)
    db.commit()
    return result


@router.post("/methods/{id}/resend")
def resend_method(id: UUID, data: PasswordConfirmation, user=Depends(recovery_mutator), db=Depends(get_db)):
    confirm_password(user, data.password)
    method = find_method(db, user, id)
    if method.verified_at:
        fail("already_verified", "This recovery method is already verified.", 409)
    if uses_shared_mailbox(user, method):
        fail("shared_recovery_email", "Use a recovery address other than the shared Competitions mailbox.", 422)
    if method.kind == "sms" and not sms_available():
        fail("sms_unavailable", "Phone recovery is unavailable until an SMS provider is configured.", 503)
    if db.scalar(select(AccountToken.id).where(
        AccountToken.recovery_method_id == id, AccountToken.created_at > now() - VERIFICATION_COOLDOWN,
    ).limit(1)):
        fail("recovery_rate_limited", "Wait one minute before requesting another link.", 429)
    invalidate_tokens(db, user, recovery_method_id=method.id)
    issue_verification(db, user, method, resent=True)
    db.commit()
    return {"ok": True}


@router.post("/verify")
def verify_method(data: VerificationInput, user=Depends(recovery_mutator), db=Depends(get_db)):
    token, _, owner = usable_token(db, "verify_recovery", data.token, owner_id=user.id)
    if owner.id != user.id:
        fail("invalid_token", INVALID_TOKEN)
    method = find_method(db, user, token.recovery_method_id)
    first_eligible_method = not has_verified_recovery(db, user)
    method.verified_at = token.used_at = now()
    if first_eligible_method:
        prefer_method(db, user, method)
    token_audit(db, user, token, "completed", "Recovery method verified")
    audit(db, user, "account.recovery_method_verified", method, "Recovery method verified", after={"kind": method.kind})
    db.commit()
    return {"ok": True}


@router.post("/methods/{id}/preferred")
def set_preferred(id: UUID, data: PasswordConfirmation, user=Depends(recovery_mutator), db=Depends(get_db)):
    confirm_password(user, data.password)
    method = find_method(db, user, id)
    if not method.verified_at or uses_shared_mailbox(user, method):
        fail("unverified_recovery_method", "Verify this recovery method first.", 409)
    prefer_method(db, user, method)
    audit(db, user, "account.recovery_method_preferred", method, "Preferred recovery method updated", after={"kind": method.kind})
    db.commit()
    return {"ok": True}


@router.post("/methods/{id}/remove")
def remove_method(id: UUID, data: PasswordConfirmation, user=Depends(recovery_mutator), db=Depends(get_db)):
    confirm_password(user, data.password)
    method = find_method(db, user, id)
    verified_replacement = db.scalar(verified_methods(user).where(RecoveryMethod.id != id)
                                     .order_by(RecoveryMethod.created_at, RecoveryMethod.id).limit(1))
    if method.verified_at and not uses_shared_mailbox(user, method) and verified_replacement is None:
        fail("last_recovery_method", "Verify another recovery method before removing this one.", 409)
    was_preferred = method.preferred
    clear_method(db, user, method)
    if was_preferred and verified_replacement:
        verified_replacement.preferred = True
    db.commit()
    return {"ok": True}
