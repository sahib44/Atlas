from datetime import timedelta
from fastapi import APIRouter, Depends
from sqlalchemy import select, func
from .db import get_db, now
from .models import Audit, Campaign, Duplicate, ImportBatch, Outbound, Ticket
from .auth import current_user, allowed
from .services import audit_scope, serialize
from .tickets import ticket_scope
from .campaigns import outbox_scope

router = APIRouter()


@router.get("/api/dashboard")
def dashboard(user=Depends(current_user), db=Depends(get_db)):
    metrics, groups = [], {}

    def metric(label, stmt, link):
        metrics.append(
            {
                "label": label,
                "value": db.scalar(select(func.count()).select_from(stmt.subquery())),
                "link": link,
            }
        )

    if allowed(user, "imports"):
        metric(
            "Duplicate reviews",
            select(Duplicate).where(
                Duplicate.resolution == "pending", Duplicate.confidence_score >= 0.7
            ),
            "/duplicates",
        )
    if allowed(user, "campaigns"):
        metric(
            "Draft campaigns",
            select(Campaign).where(Campaign.status == "draft"),
            "/campaigns?status=draft",
        )
        metric(
            "Pending approval",
            select(Campaign).where(Campaign.status == "pending_approval"),
            "/campaigns?status=pending_approval",
        )
    if allowed(user, "outbox"):
        for status in ["sent", "queued", "failed"]:
            metric(
                f"Messages {status}",
                outbox_scope(user).where(Outbound.status == status),
                f"/outbox?status={status}",
            )
    if allowed(user, "tickets") or "private_tickets" in user.permissions:
        scoped = ticket_scope(user)
        opened = scoped.where(Ticket.status.not_in(["resolved", "closed"]))
        metric("Open tickets", opened, "/tickets?view=open")
        metric(
            "Overdue tickets",
            opened.where(Ticket.last_message_at < now() - timedelta(days=3)),
            "/tickets?view=overdue",
        )
        ids = scoped.with_only_columns(Ticket.id)
        groups["Tickets by status"] = [
            {"label": status, "value": count, "link": f"/tickets?status={status}"}
            for status, count in db.execute(
                select(Ticket.status, func.count())
                .where(Ticket.id.in_(ids))
                .group_by(Ticket.status)
            )
        ]
    recent = []
    if allowed(user, "audit"):
        recent = [
            serialize(a)
            for a in db.scalars(
                audit_scope(user).order_by(Audit.created_at.desc()).limit(8)
            )
        ]
    latest = (
        db.scalar(select(ImportBatch).order_by(ImportBatch.created_at.desc()).limit(1))
        if allowed(user, "imports")
        else None
    )
    return {
        "metrics": metrics,
        "groups": groups,
        "recent_activity": recent,
        "latest_import": serialize(latest) if latest else None,
        "overdue_definition": "Open tickets with no public message for more than 3 days",
    }
