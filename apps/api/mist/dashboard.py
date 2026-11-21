from datetime import timedelta
from fastapi import APIRouter, Depends, Query
from sqlalchemy import select, func, String, cast, and_, or_
from .db import get_db, now
from .models import Audit, Campaign, Duplicate, ImportBatch, OfficialJudgeAssignment, Outbound, PilotCompetition, PilotNomination, Ticket
from .auth import current_user, allowed, fail
from .services import audit_scope, serialize
from .tickets import ticket_scope
from .campaigns import outbox_scope

router = APIRouter()


@router.get("/api/accounts/department-dashboard")
def department_dashboard(
    event_year: int | None = Query(None, ge=2000, le=2200),
    user=Depends(current_user),
    db=Depends(get_db),
):
    from .accounts import department_for
    from .pilots import active_competitions, add_competition_summaries, tournament_options

    department = department_for(db, user) if user.managed_account else None
    if not department or not allowed(user, "judges"):
        fail("forbidden", "Your account has no assigned competitions dashboard.", 403)
    year = event_year if event_year is not None else tournament_options(db)["default_year"]
    scope = active_competitions().where(
        PilotCompetition.category_slug == department["category_slug"],
        PilotCompetition.event_year == year,
    )
    sports = {
        "brothers-sports": ("Brother's Basketball", "Brother's Soccer"),
        "sisters-sports": ("Sister's Basketball", "Sister's Soccer"),
    }.get(department["slug"])
    if sports:
        scope = scope.where(PilotCompetition.name.in_(sports))
    items = [serialize(row) for row in db.scalars(
        scope.order_by(PilotCompetition.is_pilot, PilotCompetition.name, PilotCompetition.id).limit(100)
    )]
    add_competition_summaries(db, items)
    ids = scope.with_only_columns(PilotCompetition.id)
    activity_targets = (
        (PilotCompetition, PilotCompetition.id),
        (PilotNomination, PilotNomination.pilot_id),
        (OfficialJudgeAssignment, OfficialJudgeAssignment.competition_id),
        (Campaign, Campaign.pilot_competition_id),
    )
    events = db.execute(
        select(Audit.id, Audit.summary, Audit.created_at)
        .where(or_(*(and_(
            Audit.entity_type == model.__tablename__,
            Audit.entity_id.in_(select(cast(model.id, String)).where(competition_id.in_(ids))),
        ) for model, competition_id in activity_targets)))
        .order_by(Audit.created_at.desc(), Audit.id.desc())
        .limit(8)
    )
    return {
        "items": items,
        "activity": [{"id": row.id, "summary": row.summary, "created_at": row.created_at} for row in events],
    }


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
