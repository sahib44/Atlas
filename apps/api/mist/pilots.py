"""Pilot competition workspace. Documents and contacts stay in the local database."""

import io
import json
import zipfile
from collections import defaultdict
from datetime import timedelta
from urllib.parse import quote
from uuid import UUID
from fastapi import APIRouter, Depends, File, Form, Query, Response, UploadFile
from sqlalchemy import func, or_, select
from .auth import current_user, require, allowed, fail
from .assignments import REQUIRED_JUDGES, live_assignments, lock_assignments, place_assignment, release_assignment
from .competition_catalog import CATEGORIES
from .db import get_db, now
from .inbox import competition_unread
from .models import (InboundMessage, Judge, JudgeQualification, OfficialJudgeAssignment,
                     PilotCompetition, PilotDocument, PilotNomination, Qualification, User)
from .schemas import ObjectView, OfficialAssignmentIn, Page, PilotJudgeIn, PilotStatusIn, PilotDeleteIn
from .services import (
    add_relations, audit, clean, contact_email, get, page, serialize, split_values,
    write_judge,
)

router = APIRouter(prefix="/api/competitions")
STATUSES = {"to_contact", "awaiting_response", "accepted", "declined"}
MAX_DOCUMENT_BYTES = 5 * 1024 * 1024


def readable(user):
    if not allowed(user, "judges"):
        fail("forbidden", "Cannot view competitions.", 403)


def active_competition(db, id):
    competition = get(db, PilotCompetition, id)
    if competition.deleted_at:
        fail("not_found", "Competition not found.", 404)
    return competition


def active_competitions():
    return select(PilotCompetition).where(
        PilotCompetition.deleted_at.is_(None),
        PilotCompetition.activation_status == "active",
    )


def tournament_options(db):
    years = list(db.scalars(select(PilotCompetition.event_year).where(
        PilotCompetition.deleted_at.is_(None),
    ).distinct().order_by(PilotCompetition.event_year.desc())))
    latest_active = db.scalar(active_competitions().with_only_columns(func.max(PilotCompetition.event_year)))
    return {"years": years, "default_year": latest_active or (years[0] if years else None)}


def confirmed_counts(db, ids):
    return dict(db.execute(
        select(OfficialJudgeAssignment.competition_id, func.count())
        .where(
            OfficialJudgeAssignment.competition_id.in_(ids),
            OfficialJudgeAssignment.released_at.is_(None),
        )
        .group_by(OfficialJudgeAssignment.competition_id)
    ).all())


@router.get("/tournaments")
def tournaments(user=Depends(current_user), db=Depends(get_db)):
    if not allowed(user, "imports"):
        readable(user)
    return tournament_options(db)


@router.get("/categories")
def categories(
    event_year: int | None = Query(None, ge=2000, le=2200),
    user=Depends(current_user), db=Depends(get_db),
):
    readable(user)
    year = event_year if event_year is not None else tournament_options(db)["default_year"]
    competitions = db.execute(active_competitions().with_only_columns(
        PilotCompetition.id, PilotCompetition.category_slug,
    ).where(PilotCompetition.event_year == year)).all()
    ids = [competition.id for competition in competitions]
    confirmed = confirmed_counts(db, ids) if ids else {}
    unread = competition_unread(db, ids) if ids else {}
    totals = defaultdict(lambda: [0, 0, 0])
    for competition in competitions:
        counts = totals[competition.category_slug]
        counts[0] += 1
        counts[1] += confirmed.get(competition.id, 0)
        counts[2] += unread.get(competition.id, 0)
    return [
        {"slug": slug, "name": name, "event_year": year, "competition_count": count,
         "confirmed_count": occupied, "required_count": count * REQUIRED_JUDGES, "unread_count": unread_count}
        for slug, (name, _) in CATEGORIES.items()
        for count, occupied, unread_count in [totals[slug]]
    ]


@router.get("/deletions", response_model=Page)
def deleted_pilots(
    page_no: int = Query(1, alias="page", ge=1),
    page_size: int = Query(25, ge=1, le=100),
    user=Depends(current_user),
    db=Depends(get_db),
):
    if not (allowed(user, "judges") or allowed(user, "audit")):
        fail("forbidden", "Cannot view deleted pilots.", 403)
    stmt = select(PilotCompetition).where(
        PilotCompetition.is_pilot.is_(True),
        PilotCompetition.deleted_at.is_not(None),
    ).order_by(PilotCompetition.deleted_at.desc())
    result = page(db, stmt, page_no, page_size)
    for item in result["items"]:
        actor = db.get(User, UUID(item["deleted_by"])) if item["deleted_by"] else None
        item["deleted_by_name"] = actor.full_name if actor else "Unknown organizer"
        item["category_name"] = CATEGORIES.get(item["category_slug"], (item["category_slug"], []))[0]
    return result


async def checked_document(upload, kind, user, pilot):
    if not upload or not upload.filename:
        return None
    name = upload.filename.replace("\\", "/").split("/")[-1].replace("\r", "").replace("\n", "")[:255]
    extension = name.lower().rsplit(".", 1)[-1]
    content = await upload.read(MAX_DOCUMENT_BYTES + 1)
    if not content or len(content) > MAX_DOCUMENT_BYTES:
        fail("invalid_document", "Rules and procedures must be nonempty and under 5 MB each.")
    if extension == "pdf" and content.startswith(b"%PDF-"):
        media_type = "application/pdf"
    elif extension == "docx" and content.startswith(b"PK"):
        try:
            with zipfile.ZipFile(io.BytesIO(content)) as document:
                members = document.namelist()
                if "[Content_Types].xml" not in members or any("vbaProject" in x for x in members):
                    raise ValueError("Invalid DOCX")
        except (zipfile.BadZipFile, ValueError):
            fail("invalid_document", "Upload a valid PDF or non-macro DOCX file.")
        media_type = "application/vnd.openxmlformats-officedocument.wordprocessingml.document"
    else:
        fail("invalid_document", "Upload a PDF or DOCX file for rules and procedures.")
    return PilotDocument(
        pilot_id=pilot.id,
        kind=kind,
        filename=name,
        media_type=media_type,
        content=content,
        uploaded_by=user.id,
    )


def add_competition_summaries(db, items):
    if not items:
        return
    ids = [UUID(item["id"]) for item in items]
    unread = competition_unread(db, ids)
    confirmed = confirmed_counts(db, ids)
    counts = defaultdict(dict)
    for competition_id, status, count in db.execute(
        select(PilotNomination.pilot_id, PilotNomination.status, func.count())
        .where(PilotNomination.pilot_id.in_(ids))
        .group_by(PilotNomination.pilot_id, PilotNomination.status)
    ):
        counts[competition_id][status] = count
    documents = defaultdict(list)
    for competition_id, kind, filename, media_type in db.execute(
        select(PilotDocument.pilot_id, PilotDocument.kind, PilotDocument.filename, PilotDocument.media_type)
        .where(PilotDocument.pilot_id.in_(ids))
    ):
        documents[competition_id].append({"kind": kind, "filename": filename, "media_type": media_type})
    for item, competition_id in zip(items, ids):
        is_active = item["activation_status"] == "active"
        item.update(
            counts=counts[competition_id], documents=documents[competition_id],
            unread_count=unread.get(competition_id, 0),
            confirmed_count=confirmed.get(competition_id, 0) if is_active else 0,
            required_count=REQUIRED_JUDGES if is_active else 0,
        )


def pilot_view(db, pilot):
    data = serialize(pilot)
    add_competition_summaries(db, [data])
    data["official_assignments"] = [
        {**serialize(assignment), "judge_name": name}
        for assignment, name in db.execute(
            live_assignments().add_columns(Judge.display_name)
            .join(Judge, Judge.id == OfficialJudgeAssignment.judge_id)
            .where(OfficialJudgeAssignment.competition_id == pilot.id)
            .order_by(OfficialJudgeAssignment.slot_number)
        )
    ]
    return data


@router.get("", response_model=Page)
def list_pilots(
    category_slug: str = "",
    event_year: int | None = Query(None, ge=2000, le=2200),
    page_no: int = Query(1, alias="page", ge=1),
    page_size: int = Query(25, ge=1, le=100),
    user=Depends(current_user),
    db=Depends(get_db),
):
    readable(user)
    if category_slug and category_slug not in CATEGORIES:
        fail("invalid_category", "Unknown competition category.")
    stmt = active_competitions()
    if event_year is not None:
        stmt = stmt.where(PilotCompetition.event_year == event_year)
    if category_slug:
        stmt = stmt.where(PilotCompetition.category_slug == category_slug)
    result = page(
        db,
        stmt.order_by(PilotCompetition.is_pilot, PilotCompetition.name, PilotCompetition.event_year, PilotCompetition.id),
        page_no, page_size,
    )
    add_competition_summaries(db, result["items"])
    return result


@router.post("", response_model=ObjectView)
async def create_pilot(
    name: str = Form(...),
    event_year: int = Form(...),
    category_slug: str = Form(...),
    judge_ids: str = Form("[]"),
    rules: UploadFile | None = File(None),
    procedures: UploadFile | None = File(None),
    user=Depends(require("judges")),
    db=Depends(get_db),
):
    from .recruitment import queue_nomination

    name = clean(name).strip()
    if not name or len(name) > 200 or not 2000 <= event_year <= 2200:
        fail("invalid_pilot", "Enter a name and an event year between 2000 and 2200.")
    if category_slug not in CATEGORIES:
        fail("invalid_category", "Choose a competition category.")
    if db.scalar(
        select(PilotCompetition).where(
            func.lower(PilotCompetition.name) == name.lower(),
            PilotCompetition.event_year == event_year,
            PilotCompetition.deleted_at.is_(None),
        )
    ):
        fail("duplicate_pilot", "A pilot with this name already exists for that year.", 409)
    try:
        ids = list(dict.fromkeys(UUID(str(x)) for x in json.loads(judge_ids)))
    except (TypeError, ValueError, json.JSONDecodeError):
        fail("invalid_judges", "Selected judges are invalid.")
    if len(ids) > 100:
        fail("invalid_judges", "Select at most 100 judges when creating a pilot.")
    if ids:
        lock_assignments(db)
    judges = db.scalars(
        select(Judge).where(Judge.id.in_(ids), Judge.merged_into_id.is_(None))
    ).all()
    if len(judges) != len(ids):
        fail("invalid_judges", "One or more selected judges no longer exists.")
    pilot = PilotCompetition(name=name, event_year=event_year, category_slug=category_slug, is_pilot=True, created_by=user.id)
    db.add(pilot)
    db.flush()
    documents = [
        await checked_document(upload, kind, user, pilot)
        for kind, upload in (("rules", rules), ("procedures", procedures))
    ]
    db.add_all(doc for doc in documents if doc)
    for judge in judges:
        queue_nomination(db, pilot, judge, user)
    audit(db, user, "pilot.created", pilot, f"Created pilot {name}")
    db.commit()
    return pilot_view(db, pilot)


@router.get("/{id}", response_model=ObjectView)
def pilot_detail(id: UUID, user=Depends(current_user), db=Depends(get_db)):
    readable(user)
    return pilot_view(db, active_competition(db, id))


@router.get("/{id}/assignments/history", response_model=Page)
def assignment_history(
    id: UUID, page_no: int = Query(1, alias="page", ge=1),
    page_size: int = Query(25, ge=1, le=100),
    user=Depends(current_user), db=Depends(get_db),
):
    readable(user)
    get(db, PilotCompetition, id)
    return page(db, select(OfficialJudgeAssignment).where(
        OfficialJudgeAssignment.competition_id == id,
    ).order_by(OfficialJudgeAssignment.created_at.desc(), OfficialJudgeAssignment.id), page_no, page_size)


@router.post("/{id}/assignments", response_model=ObjectView)
def confirm_assignment(
    id: UUID, data: OfficialAssignmentIn,
    user=Depends(require("judge_assign")), db=Depends(get_db),
):
    lock_assignments(db)
    competition = active_competition(db, id)
    assignment = place_assignment(db, competition, data, user)
    db.commit()
    return serialize(assignment)


@router.delete("/{id}/assignments/{assignment_id}", response_model=ObjectView)
def release_official_assignment(
    id: UUID, assignment_id: UUID,
    user=Depends(require("judge_assign")), db=Depends(get_db),
):
    lock_assignments(db)
    assignment = db.scalar(live_assignments().where(
        OfficialJudgeAssignment.competition_id == id,
        OfficialJudgeAssignment.id == assignment_id,
    ))
    if not assignment:
        fail("assignment_changed", "This slot is no longer held by that judge. Refresh the roster.", 409)
    release_assignment(db, assignment, user, "released")
    db.commit()
    return serialize(assignment)


@router.delete("/{id}", response_model=ObjectView)
def delete_pilot(id: UUID, data: PilotDeleteIn, user=Depends(require("delete")), db=Depends(get_db)):
    lock_assignments(db)
    pilot = active_competition(db, id)
    if not pilot.is_pilot:
        fail("permanent_competition", "Permanent competitions cannot be deleted.", 403)
    if not data.confirmed_first or not data.confirmed_second or data.exact_name != pilot.name:
        fail("delete_confirmation", "Complete all three deletion confirmations.", 400)
    before = serialize(pilot)
    for assignment in db.scalars(live_assignments().where(OfficialJudgeAssignment.competition_id == id)):
        release_assignment(db, assignment, user, "pilot_deleted")
    pilot.deleted_at = now()
    pilot.deleted_by = user.id
    audit(db, user, "pilot.deleted", pilot, f"Deleted pilot {pilot.name}", before, serialize(pilot))
    db.commit()
    return {"id": str(pilot.id)}


@router.get("/{id}/documents/{kind}")
def download_document(
    id: UUID, kind: str, user=Depends(current_user), db=Depends(get_db)
):
    readable(user)
    active_competition(db, id)
    if kind not in {"rules", "procedures"}:
        fail("not_found", "Unknown document.", 404)
    doc = db.scalar(
        select(PilotDocument).where(
            PilotDocument.pilot_id == id, PilotDocument.kind == kind
        )
    )
    if not doc:
        fail("not_found", "Document not found.", 404)
    disposition = "inline" if doc.media_type == "application/pdf" else "attachment"
    return Response(
        doc.content,
        media_type=doc.media_type,
        headers={"Content-Disposition": f"{disposition}; filename*=UTF-8''{quote(doc.filename)}"},
    )


@router.put("/{id}/documents/{kind}", response_model=ObjectView)
async def upload_document(
    id: UUID,
    kind: str,
    file: UploadFile = File(...),
    user=Depends(require("judges")),
    db=Depends(get_db),
):
    if kind not in {"rules", "procedures"}:
        fail("not_found", "Unknown document.", 404)
    pilot = active_competition(db, id)
    incoming = await checked_document(file, kind, user, pilot)
    if not incoming:
        fail("invalid_document", "Choose a PDF or DOCX file.")
    existing = db.scalar(
        select(PilotDocument).where(
            PilotDocument.pilot_id == id, PilotDocument.kind == kind
        )
    )
    if existing:
        existing.filename = incoming.filename
        existing.media_type = incoming.media_type
        existing.content = incoming.content
        existing.uploaded_by = user.id
        document = existing
    else:
        db.add(incoming)
        document = incoming
    audit(db, user, "pilot.document_uploaded", pilot, f"Updated {kind} document")
    db.commit()
    return {"id": str(document.id), "kind": kind, "filename": document.filename}


@router.get("/{id}/judges", response_model=Page)
def pilot_judges(
    id: UUID,
    status: str = "",
    q: str = "",
    overdue_days: int | None = Query(None, ge=1, le=365),
    page_no: int = Query(1, alias="page", ge=1),
    page_size: int = Query(25, ge=1, le=100),
    user=Depends(current_user),
    db=Depends(get_db),
):
    readable(user)
    competition = active_competition(db, id)
    if status and status not in STATUSES:
        fail("invalid_status", "Unknown nomination status.")
    stmt = select(PilotNomination).where(PilotNomination.pilot_id == id)
    if status:
        stmt = stmt.where(PilotNomination.status == status)
    if q.strip():
        stmt = stmt.join(Judge).where(
            or_(
                Judge.display_name.ilike(f"%{q.strip()}%"),
                Judge.primary_email.ilike(f"%{q.strip()}%"),
            )
        )
    if overdue_days:
        stmt = stmt.where(
            PilotNomination.status == "awaiting_response",
            PilotNomination.last_contacted_at <= now() - timedelta(days=overdue_days),
        )
    unread = select(func.count()).select_from(InboundMessage).where(InboundMessage.judge_id == PilotNomination.judge_id, InboundMessage.read_at.is_(None), or_(InboundMessage.competition_id == id, InboundMessage.competition_id.is_(None))).correlate(PilotNomination).scalar_subquery()
    stmt = stmt.order_by(unread.desc(), PilotNomination.created_at.desc())

    official = {
        assignment.judge_id: {
            "id": str(assignment.id), "competition_id": str(assignment.competition_id),
            "competition_name": name, "slot_number": assignment.slot_number,
        }
        for assignment, name in db.execute(
            live_assignments().add_columns(PilotCompetition.name)
            .join(PilotCompetition, PilotCompetition.id == OfficialJudgeAssignment.competition_id)
            .where(OfficialJudgeAssignment.event_year == competition.event_year)
        )
    }

    def view(nomination):
        judge = get(db, Judge, nomination.judge_id)
        return {
            **serialize(nomination),
            "official_assignment": official.get(nomination.judge_id),
            "unread_count": db.scalar(select(func.count()).select_from(InboundMessage).where(InboundMessage.judge_id == nomination.judge_id, InboundMessage.read_at.is_(None), or_(InboundMessage.competition_id == id, InboundMessage.competition_id.is_(None)))),
            "judge": {
                "id": str(judge.id),
                "display_name": judge.display_name,
                "primary_email": judge.primary_email,
                "phone": judge.phone,
                "do_not_contact": judge.do_not_contact,
                "is_active_contact": judge.merged_into_id is None,
                "qualification_text": nomination.qualification_text,
                "qualifications": [
                    name for (name,) in db.execute(
                        select(Qualification.name)
                        .join(JudgeQualification)
                        .where(JudgeQualification.judge_id == judge.id)
                    )
                ],
            },
        }

    return page(db, stmt, page_no, page_size, view)


@router.post("/{id}/judges", response_model=ObjectView)
def add_pilot_judge(
    id: UUID,
    data: PilotJudgeIn,
    user=Depends(require("judges")),
    db=Depends(get_db),
):
    from .recruitment import email_contacts, queue_nomination

    lock_assignments(db)
    pilot = active_competition(db, id)
    if pilot.activation_status != "active":
        fail("competition_inactive", "Choose an active competition.", 409)
    if data.existing_judge_id:
        judge = get(db, Judge, data.existing_judge_id)
        if judge.merged_into_id:
            fail("invalid_judge", "Use an active judge contact.")
    else:
        name = clean(data.name).strip()
        address = contact_email(data.email, optional=True)
        phone = clean(data.phone).strip()
        if not name or not (address or phone):
            fail("contact_required", "Enter a name and at least an email or phone.")
        db.execute(select(func.pg_advisory_xact_lock(410261)))
        matches = list(db.scalars(email_contacts(address).limit(2))) if address else []
        if len(matches) > 1:
            fail("contact_conflict", "Resolve the duplicate contacts before submitting.", 409)
        judge = matches[0] if matches else None
        if judge:
            if name.casefold() != judge.display_name.casefold():
                fail("existing_contact", "This email belongs to an existing judge. Select that contact instead.", 409)
            if phone and not judge.phone:
                judge.phone = phone
        else:
            first, _, last = name.partition(" ")
            judge = write_judge(
                db,
                {
                    "display_name": name,
                    "first_name": first,
                    "last_name": last,
                    "primary_email": address,
                    "phone": phone,
                    "qualifications": [],
                },
                user,
                source=f"pilot {pilot.id}",
            )
    if db.scalar(
        select(PilotNomination).where(
            PilotNomination.pilot_id == id, PilotNomination.judge_id == judge.id
        )
    ):
        fail("already_added", "This judge is already in the pilot.", 409)
    nomination = queue_nomination(db, pilot, judge, user, clean(data.qualifications).strip())
    db.commit()
    return serialize(nomination)


@router.patch("/{id}/judges/{nomination_id}", response_model=ObjectView)
def set_pilot_status(
    id: UUID,
    nomination_id: UUID,
    data: PilotStatusIn,
    user=Depends(require("judges")),
    db=Depends(get_db),
):
    from .recruitment import update_response

    lock_assignments(db)
    active_competition(db, id)
    nomination = get(db, PilotNomination, nomination_id, True)
    if nomination.pilot_id != id:
        fail("not_found", "Nomination not found in this pilot.", 404)
    update_response(db, nomination, data.status, user)
    db.commit()
    return serialize(nomination)
