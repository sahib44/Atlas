import hashlib
import os
import secrets
from datetime import timedelta
from argon2 import PasswordHasher
from argon2.exceptions import VerificationError
from fastapi import Depends, HTTPException, Request
from sqlalchemy import select
from .account_config import managed_mode
from .db import get_db, now
from .models import DepartmentAssignment, User, Session

hasher = PasswordHasher()
dummy_password_hash = hasher.hash(secrets.token_urlsafe(32))
ROLE_PERMISSIONS = {
    "director": {
        "delete",
        "judge_assign",
        "recruitment_review",
        "ticket_approve",
        "judges",
        "imports",
        "campaigns",
        "campaign_cancel",
        "tickets",
        "catalogs",
        "audit",
        "users",
        "outbox",
        "jobs",
    },
    "technology_administrator": {"imports", "catalogs", "audit", "outbox", "jobs"},
}


COMPETITIONS_ROLES = {"competitions_administrator", "competitions_member", "competitions_chair"}
for role in COMPETITIONS_ROLES:
    ROLE_PERMISSIONS[role] = {"judges", "imports", "campaigns", "campaign_cancel", "tickets", "catalogs", "outbox"}
ROLE_PERMISSIONS["competitions_chair"].update({"approve", "delete", "ticket_approve", "judge_assign", "recruitment_review"})


def allowed(user, permission):
    return permission in ROLE_PERMISSIONS.get(user.role, set())


def fail(code, message, status=400):
    raise HTTPException(status, detail={"code": code, "message": message})


def has_managed_assignment(db, user):
    if user.role == "competitions_chair":
        return True
    return db.scalar(select(DepartmentAssignment.id).where(
        DepartmentAssignment.user_id == user.id,
        DepartmentAssignment.email == user.email,
        DepartmentAssignment.ended_at.is_(None),
    )) is not None


def session_user(request: Request, db=Depends(get_db)):
    token = request.cookies.get("mist_session", "")
    session = db.scalar(
        select(Session).where(
            Session.token_hash == hashlib.sha256(token.encode()).hexdigest(),
            Session.expires_at > now(),
        )
    )
    user = db.get(User, session.user_id) if session else None
    if (
        not user or not user.is_active or user.role not in ROLE_PERMISSIONS
        or user.managed_account != managed_mode()
        or (user.managed_account and not has_managed_assignment(db, user))
    ):
        fail("unauthenticated", "Please sign in.", 401)
    if (
        request.method not in {"GET", "HEAD", "OPTIONS"}
        and request.headers.get("X-MIST-Request") != "1"
    ):
        fail("csrf", "Missing request protection header.", 403)
    return user


def current_user(user=Depends(session_user), db=Depends(get_db)):
    from .recovery import has_verified_recovery

    if user.managed_account and not has_verified_recovery(db, user):
        fail("recovery_required", "Verify a backup recovery method to access the workspace.", 403)
    return user


def require(permission):
    def dependency(user=Depends(current_user)):
        if not allowed(user, permission):
            fail("forbidden", "Your role does not allow this action.", 403)
        return user

    return dependency


def login(db, email, password, response):
    managed = managed_mode()
    eligible_user = select(User).where(
        User.email == email.strip().lower(),
        User.is_active.is_(True),
        User.role.in_(ROLE_PERMISSIONS),
        User.managed_account.is_(managed),
    )
    user = db.scalar(eligible_user)
    password_hash = user.password_hash if user else dummy_password_hash
    try:
        hasher.verify(password_hash, password)
    except VerificationError:
        fail("invalid_credentials", "Email or password is incorrect.", 401)
    if not user:
        fail("invalid_credentials", "Email or password is incorrect.", 401)
    if managed:
        from .accounts import lock_accounts

        # A password change or revocation that commits during verification must
        # not leave a new session behind.
        lock_accounts(db)
        user = db.scalar(eligible_user.execution_options(populate_existing=True))
        if not user or user.password_hash != password_hash or not has_managed_assignment(db, user):
            fail("invalid_credentials", "Email or password is incorrect.", 401)
    token = secrets.token_urlsafe(48)
    db.add(
        Session(
            user_id=user.id,
            token_hash=hashlib.sha256(token.encode()).hexdigest(),
            expires_at=now() + timedelta(hours=12),
        )
    )
    user.last_login_at = now()
    db.commit()
    response.set_cookie(
        "mist_session",
        token,
        httponly=True,
        samesite="strict",
        secure=managed or os.getenv("COOKIE_SECURE") == "true",
        max_age=43200,
    )
    return user
