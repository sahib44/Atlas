"""Competition-oriented judge directory and spreadsheet export."""

import csv
import io
import hashlib
import json
from datetime import timedelta
from uuid import UUID
from fastapi import APIRouter, Depends, Query, Response
from sqlalchemy import and_, case, func, or_, select, update, delete, text
from sqlalchemy.orm import aliased
from .auth import allowed, current_user, fail, require, hasher
from .assignments import live_assignments, lock_assignments, release_assignment
from argon2.exceptions import VerificationError
from .competition_catalog import CATEGORIES
from .db import get_db, now
from .models import *
from .schemas import Page, ClearJudges, JudgeDetailsIn
from .services import judge_views, serialize, audit
from .pilots import active_competitions, tournament_options
from .recruitment import save_judge_details

router = APIRouter(prefix="/api/judge-directory")


def readable(user):
    if not (allowed(user, "judges") or allowed(user, "imports")):
        fail("forbidden", "Cannot view judge directory.", 403)


def response_status(value):
    return {"prospective": "to_contact", "contacted": "awaiting_response", "interested": "accepted", "confirmed": "accepted", "inactive": "declined"}.get(value, value)


def directory_query(q="", category_slug="", competition_id=None, status=""):
    active_ids = select(PilotCompetition.id).where(PilotCompetition.deleted_at.is_(None))
    stmt = (
        select(Judge, PilotNomination, PilotCompetition)
        .select_from(Judge)
        .outerjoin(PilotNomination, and_(
            PilotNomination.judge_id == Judge.id,
            PilotNomination.pilot_id.in_(active_ids),
        ))
        .outerjoin(PilotCompetition, PilotCompetition.id == PilotNomination.pilot_id)
        .where(Judge.merged_into_id.is_(None))
    )
    if q.strip():
        term = f"%{q.strip()}%"
        stmt = stmt.where(or_(Judge.display_name.ilike(term), Judge.primary_email.ilike(term), Judge.phone.ilike(term), PilotCompetition.name.ilike(term)))
    if category_slug:
        stmt = stmt.where(PilotCompetition.category_slug == category_slug)
    if competition_id:
        stmt = stmt.where(PilotCompetition.id == competition_id)
    if status:
        if status not in {"to_contact", "awaiting_response", "accepted", "declined"}:
            fail("invalid_status", "Unknown judge response.")
        stmt = stmt.where(func.coalesce(PilotNomination.status, case({"prospective": "to_contact", "contacted": "awaiting_response", "interested": "accepted", "confirmed": "accepted", "inactive": "declined"}, value=Judge.status, else_=Judge.status)) == status)
    return stmt


def view_rows(db, rows):
    judges = {judge.id: judge for judge, _, _ in rows}
    legacy = {item["id"]: item for item in judge_views(db, list(judges.values()))}
    result = []
    for judge, nomination, competition in rows:
        fallback = judge.notes or "; ".join(legacy[str(judge.id)]["qualifications"])
        result.append({
            "id": str(nomination.id) if nomination else str(judge.id),
            "judge_id": str(judge.id),
            "name": judge.display_name,
            "email": judge.primary_email or judge.alternate_email,
            "phone": judge.phone,
            "contact": " · ".join(v for v in (judge.primary_email or judge.alternate_email, judge.phone) if v),
            "qualification": nomination.qualification_text or fallback if nomination else fallback,
            "status": nomination.status if nomination else response_status(judge.status),
            "category_slug": competition.category_slug if competition else "",
            "category": CATEGORIES[competition.category_slug][0] if competition and competition.category_slug in CATEGORIES else "Unassigned",
            "competition_id": str(competition.id) if competition else None,
            "competition": competition.name if competition else "Unassigned",
            "event_year": competition.event_year if competition else None,
            "added_at": (nomination.created_at if nomination else judge.created_at).isoformat(),
        })
    return result


def spreadsheet_cell(value):
    text = str(value or "")
    return "'" + text if text.lstrip().startswith(("=", "+", "-", "@")) else text


def sheet_content(db, q="", category_slug="", competition_id=None, status=""):
    category_order = case(
        {slug: index for index, slug in enumerate(CATEGORIES)},
        value=PilotCompetition.category_slug,
        else_=len(CATEGORIES),
    )
    competition_order = case(
        {name: index for _, names in CATEGORIES.values() for index, name in enumerate(names)},
        value=PilotCompetition.name, else_=999,
    )
    rows = db.execute(directory_query(q, category_slug, competition_id, status).order_by(
        category_order, PilotCompetition.is_pilot, competition_order, PilotCompetition.name,
        func.coalesce(PilotNomination.created_at, Judge.created_at), Judge.id,
    )).all()
    output = io.StringIO()
    writer = csv.writer(output)
    writer.writerow(["Main category", "Competition", "Response", "Name", "Contact", "Qualification", "Year", "Added"])
    for item in view_rows(db, rows):
        writer.writerow([spreadsheet_cell(item[key]) for key in (
            "category", "competition", "status", "name", "contact", "qualification", "event_year", "added_at",
        )])
    return ("\ufeff" + output.getvalue()).encode("utf-8")


def directory_fingerprint(db):
    records = {cls.__tablename__: [serialize(item) for item in db.scalars(select(cls).order_by(cls.id))] for cls in (Judge, PilotNomination, PilotCompetition, OfficialJudgeAssignment)}
    return hashlib.sha256(json.dumps(records, sort_keys=True).encode()).hexdigest()


@router.post("/clear/prepare")
def prepare_clear(user=Depends(require("delete")), db=Depends(get_db)):
    lock_assignments(db)
    db.execute(text("LOCK TABLE judge_contacts, pilot_nominations, pilot_competitions IN SHARE ROW EXCLUSIVE MODE"))
    backup = DirectoryBackup(created_by=user.id, content=sheet_content(db), fingerprint=directory_fingerprint(db), judge_count=db.scalar(select(func.count()).select_from(Judge)))
    db.add(backup)
    db.commit()
    return {"id": str(backup.id), "judge_count": backup.judge_count, "download_url": f"/api/judge-directory/backups/{backup.id}"}


@router.get("/backups/{id}")
def download_backup(id: UUID, user=Depends(require("delete")), db=Depends(get_db)):
    backup = db.get(DirectoryBackup, id)
    if not backup:
        fail("not_found", "Backup not found.", 404)
    return Response(backup.content, media_type="text/csv; charset=utf-8", headers={"Content-Disposition": f"attachment; filename=mist-deck-judges-backup-{backup.created_at:%Y%m%d-%H%M%S}.csv"})


@router.post("/clear")
def clear_directory(data: ClearJudges, user=Depends(require("delete")), db=Depends(get_db)):
    if not data.confirmed_first or not data.confirmed_second:
        fail("delete_confirmation", "Complete all three confirmations.")
    try:
        hasher.verify(user.password_hash, data.password)
    except VerificationError:
        fail("invalid_password", "Your password is incorrect.", 403)
    backup = db.scalar(select(DirectoryBackup).where(DirectoryBackup.id == data.backup_id).with_for_update())
    if not backup or backup.created_by != user.id or backup.cleared_at or backup.created_at < now() - timedelta(minutes=15):
        fail("backup_required", "Create a fresh sheet backup before clearing judges.")
    # Prevent contact edits and new assignments between validation and deletion.
    lock_assignments(db)
    db.execute(text("LOCK TABLE judge_contacts, pilot_nominations, pilot_competitions IN SHARE ROW EXCLUSIVE MODE"))
    if backup.fingerprint != directory_fingerprint(db):
        fail("directory_changed", "The directory changed. Cancel and restart to export a fresh backup.", 409)
    count = backup.judge_count
    for assignment in db.scalars(live_assignments()):
        release_assignment(db, assignment, user, "directory_cleared")
    db.execute(update(Recipient).values(judge_id=None))
    db.execute(update(ImportRow).values(matched_judge_id=None))
    db.execute(update(Duplicate).values(existing_judge_id=None))
    db.execute(update(Notification).where(Notification.kind.in_(["judge_reply", "judge_request", "judge_reviewed"])).values(read_at=now(), href="/campaigns"))
    for cls in (MergeEvent, PilotNomination, Engagement, JudgeCategory, JudgeQualification):
        db.execute(delete(cls))
    db.execute(update(Judge).values(merged_into_id=None))
    db.execute(delete(Judge))
    backup.cleared_at = now()
    audit(db, user, "judges.cleared", backup, f"Cleared {count} judge contacts after saving sheet backup", after={"backup_id": str(backup.id), "judge_count": count})
    db.commit()
    return {"cleared": count, "backup_url": f"/api/judge-directory/backups/{backup.id}"}


def confirmed_query(event_year, q="", competition_id=None, category_slug=""):
    original_contact = aliased(Judge)
    nomination = aliased(PilotNomination)
    nomination_id = select(nomination.id).join(original_contact, nomination.judge_id == original_contact.id).where(
        nomination.pilot_id == PilotCompetition.id,
        or_(original_contact.id == Judge.id, original_contact.merged_into_id == Judge.id),
    ).order_by((original_contact.id == Judge.id).desc(), nomination.created_at.desc(), nomination.id
    ).correlate(Judge, PilotCompetition).limit(1).scalar_subquery()
    statement = select(OfficialJudgeAssignment, Judge, PilotCompetition, PilotNomination).select_from(OfficialJudgeAssignment).join(
        Judge, Judge.id == OfficialJudgeAssignment.judge_id,
    ).join(PilotCompetition, PilotCompetition.id == OfficialJudgeAssignment.competition_id).outerjoin(
        PilotNomination, PilotNomination.id == nomination_id,
    ).where(
        OfficialJudgeAssignment.released_at.is_(None), OfficialJudgeAssignment.event_year == event_year,
        PilotCompetition.deleted_at.is_(None), PilotCompetition.activation_status == "active",
    )
    if q.strip():
        term = f"%{q.strip()}%"
        statement = statement.where(or_(Judge.display_name.ilike(term), Judge.primary_email.ilike(term),
                                           Judge.alternate_email.ilike(term), Judge.phone.ilike(term)))
    if competition_id:
        statement = statement.where(PilotCompetition.id == competition_id)
    if category_slug:
        statement = statement.where(PilotCompetition.category_slug == category_slug)
    return statement


def confirmed_view(row):
    assignment, judge, competition, nomination = row
    return {
        **serialize(assignment), "name": judge.display_name, "email": judge.primary_email or judge.alternate_email,
        "phone": judge.phone, "competition": competition.name, "category_slug": competition.category_slug,
        "category": CATEGORIES[competition.category_slug][0],
        "qualification": nomination.qualification_text if nomination else "",
        "notes": nomination.request_notes if nomination else "",
        "contact_notes": judge.notes,
        "do_not_contact": judge.do_not_contact,
    }


@router.get("", response_model=Page)
def confirmed_directory(
    event_year: int | None = Query(None, ge=2000, le=2200), q: str = "", competition_id: UUID | None = None,
    category_slug: str = "", sort: str = "name", direction: str = "asc",
    page_no: int = Query(1, alias="page", ge=1), page_size: int = Query(5, ge=1, le=100),
    user=Depends(current_user), db=Depends(get_db),
):
    readable(user)
    event_year = event_year or tournament_options(db)["default_year"]
    stmt = confirmed_query(event_year, q, competition_id, category_slug)
    total = db.scalar(select(func.count()).select_from(stmt.subquery()))
    column = {"competition": PilotCompetition.name, "slot": OfficialJudgeAssignment.slot_number,
              "assignment": OfficialJudgeAssignment.created_at}.get(sort, Judge.display_name)
    order = column.asc() if direction == "asc" else column.desc()
    rows = db.execute(stmt.order_by(order, OfficialJudgeAssignment.id).offset((page_no - 1) * page_size).limit(page_size))
    return {"items": [confirmed_view(row) for row in rows], "total": total, "page": page_no, "page_size": page_size}


@router.get("/summary")
def confirmed_summary(event_year: int = Query(ge=2000, le=2200), user=Depends(current_user), db=Depends(get_db)):
    from .assignments import REQUIRED_JUDGES

    readable(user)
    count = db.scalar(select(func.count()).select_from(confirmed_query(event_year).subquery()))
    active = db.scalar(active_competitions().with_only_columns(func.count()).where(PilotCompetition.event_year == event_year))
    return {"confirmed": count, "competitions": active, "required": active * REQUIRED_JUDGES}


@router.get("/export")
def confirmed_export(
    event_year: int | None = Query(None, ge=2000, le=2200), q: str = "", competition_id: UUID | None = None,
    category_slug: str = "", user=Depends(current_user), db=Depends(get_db),
):
    readable(user)
    output = io.StringIO()
    writer = csv.writer(output)
    writer.writerow(["Judge", "Email", "Phone", "Category", "Competition", "Slot", "Confirmed", "Qualifications", "Notes", "Year"])
    event_year = event_year or tournament_options(db)["default_year"]
    stmt = confirmed_query(event_year, q, competition_id, category_slug)
    for row in db.execute(stmt.order_by(PilotCompetition.category_slug, PilotCompetition.name, OfficialJudgeAssignment.slot_number)):
        item = confirmed_view(row)
        writer.writerow([spreadsheet_cell(item[key]) for key in (
            "name", "email", "phone", "category", "competition", "slot_number", "created_at", "qualification", "notes", "event_year",
        )])
    return Response(("\ufeff" + output.getvalue()).encode("utf-8"), media_type="text/csv; charset=utf-8",
                    headers={"Content-Disposition": f"attachment; filename=mist-deck-confirmed-{event_year}.csv"})


@router.get("/options")
def directory_options(event_year: int = Query(ge=2000, le=2200), user=Depends(current_user), db=Depends(get_db)):
    readable(user)
    rows = db.scalars(active_competitions().where(PilotCompetition.event_year == event_year).order_by(PilotCompetition.name))
    return {"competitions": [{"id": str(row.id), "name": row.name, "category_slug": row.category_slug} for row in rows],
            "categories": [{"slug": slug, "name": entry[0]} for slug, entry in CATEGORIES.items()]}


def confirmed_profile_row(db, id):
    assignment = db.get(OfficialJudgeAssignment, id)
    if not assignment:
        fail("not_found", "Assignment not found.", 404)
    row = db.execute(confirmed_query(assignment.event_year).where(OfficialJudgeAssignment.id == id)).one_or_none()
    if not row:
        fail("not_found", "This judge no longer has an active assignment.", 404)
    return row


@router.get("/assignments/{id}")
def confirmed_profile(id: UUID, user=Depends(current_user), db=Depends(get_db)):
    readable(user)
    return confirmed_view(confirmed_profile_row(db, id))


@router.patch("/assignments/{id}/details")
def edit_confirmed_details(id: UUID, data: JudgeDetailsIn, user=Depends(current_user), db=Depends(get_db)):
    readable(user)
    lock_assignments(db)
    assignment, judge, competition, nomination = confirmed_profile_row(db, id)
    if not nomination:
        nomination = PilotNomination(pilot_id=competition.id, judge_id=judge.id, status="accepted")
        db.add(nomination)
        db.flush()
    save_judge_details(db, nomination, data, user)
    db.commit()
    return confirmed_view((assignment, judge, competition, nomination))


@router.get("/assignments/{id}/details-history", response_model=Page)
def confirmed_details_history(
    id: UUID, page_no: int = Query(1, alias="page", ge=1), user=Depends(current_user), db=Depends(get_db),
):
    readable(user)
    assignment, judge, _, _ = confirmed_profile_row(db, id)
    nominations = select(PilotNomination.id.cast(String)).join(Judge, Judge.id == PilotNomination.judge_id).join(
        PilotCompetition, PilotCompetition.id == PilotNomination.pilot_id,
    ).where(or_(Judge.id == judge.id, Judge.merged_into_id == judge.id),
            PilotCompetition.event_year == assignment.event_year)
    stmt = select(Audit, User.full_name).outerjoin(User, User.id == Audit.actor_user_id).where(
        Audit.entity_type == "pilot_nominations", Audit.entity_id.in_(nominations),
        Audit.action == "recruitment.details_updated",
    )
    total = db.scalar(select(func.count()).select_from(stmt.subquery()))
    rows = db.execute(stmt.order_by(Audit.created_at.desc(), Audit.id).offset((page_no - 1) * 25).limit(25))
    return {"items": [{"id": str(entry.id), "summary": entry.summary, "created_at": entry.created_at, "actor": actor}
                      for entry, actor in rows], "total": total, "page": page_no, "page_size": 25}


@router.get("/assignments/{id}/history", response_model=Page)
def confirmed_history(
    id: UUID, page_no: int = Query(1, alias="page", ge=1), user=Depends(current_user), db=Depends(get_db),
):
    from .services import get

    readable(user)
    assignment = get(db, OfficialJudgeAssignment, id)
    ids = select(Judge.id).where(or_(Judge.id == assignment.judge_id, Judge.merged_into_id == assignment.judge_id))
    stmt = select(OfficialJudgeAssignment, PilotCompetition.name).join(PilotCompetition,
        OfficialJudgeAssignment.competition_id == PilotCompetition.id).where(
        OfficialJudgeAssignment.event_year == assignment.event_year,
        or_(OfficialJudgeAssignment.judge_id.in_(ids) if assignment.judge_id else False, OfficialJudgeAssignment.id == id),
    )
    total = db.scalar(select(func.count()).select_from(stmt.subquery()))
    rows = db.execute(stmt.order_by(OfficialJudgeAssignment.created_at.desc()).offset((page_no - 1) * 25).limit(25))
    return {"items": [{**serialize(item), "competition": name} for item, name in rows],
            "total": total, "page": page_no, "page_size": 25}
