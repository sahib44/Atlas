"""Chair review and recruitment views over existing competition nominations."""

from uuid import UUID

from fastapi import APIRouter, Depends, Query
from sqlalchemy import String, and_, case, cast, func, or_, select, update
from sqlalchemy.orm import aliased

from .assignments import (
    REQUIRED_JUDGES, contact_family, contact_has_email_conflict, ensure_contact_unambiguous, live_assignments,
    lock_assignments, outstanding_withdrawals, place_assignment, release_assignment, tournament_archive,
)
from .auth import allowed, fail, require
from .competition_catalog import CATEGORIES
from .db import get_db, now
from .inbox import notify
from .models import Audit, Engagement, Judge, Notification, OfficialJudgeAssignment, PilotCompetition, PilotNomination, RetainedSuppression, User
from .schemas import JudgeDetailsIn, OfficialAssignmentIn, Page, PilotStatusIn, RecruitmentIn, RecruitmentReview
from .services import audit, clean, contact_email, email, email_address_matches, get, lock_contact_writes, serialize, write_judge

router = APIRouter(prefix="/api/recruitment")

def email_contacts(address):
    return select(Judge).where(Judge.merged_into_id.is_(None), email_address_matches([email(address)]))


def contact_suppressed(db, address, judge_id=None):
    """Check current preferences for the linked contact and actual destination."""
    address = email(address)
    if address and db.get(RetainedSuppression, address):
        return True

    # A merge marks the former contact suppressed and preserves its emails on the survivor.
    destination_match = and_(Judge.merged_into_id.is_(None), email_address_matches([address])) if address else False
    return db.scalar(select(Judge.id).where(
        Judge.do_not_contact,
        or_(Judge.id == judge_id, destination_match),
    ).limit(1)) is not None


def recruitment_href(competition, nomination):
    return f"/recruitment?event_year={competition.event_year}&candidate={nomination.id}"


def target_competition(db, id):
    competition = get(db, PilotCompetition, id)
    if competition.deleted_at or competition.activation_status != "active":
        fail("competition_inactive", "Choose an active competition.", 409)
    return competition


def write_request(db, data, user, nomination=None):
    competition = target_competition(db, data.competition_id)
    address = contact_email(data.email)
    name, qualification = clean(data.name).strip(), clean(data.qualifications).strip()
    if not name or not qualification:
        fail("request_fields", "Enter a name and qualifications.")
    lock_contact_writes(db)
    matches = list(db.scalars(email_contacts(address).limit(2)))
    selected_id = nomination.judge_id if nomination else data.existing_judge_id
    if len(matches) > 1 or (matches and selected_id and matches[0].id != selected_id):
        fail("contact_conflict", "This email matches a different or duplicate contact. Resolve the contacts before saving.", 409)
    judge = get(db, Judge, selected_id) if selected_id else (matches[0] if matches else None)
    if judge and judge.merged_into_id:
        fail("invalid_judge", "Use the surviving contact before saving this request.", 409)
    if judge and not nomination and (name.casefold() != judge.display_name.casefold() or
                                    (selected_id and not matches and (judge.primary_email or judge.alternate_email))):
        fail("contact_conflict", "Use the existing contact's name and email when submitting this request.", 409)
    if judge:
        ensure_contact_unambiguous(db, judge)
        if db.scalar(select(tournament_archive(judge.id, competition.event_year))):
            fail("candidate_archived", "Reopen the archived request before recruiting this judge for this tournament.", 409)
        duplicate = db.scalar(select(PilotNomination).where(
            PilotNomination.pilot_id == competition.id, PilotNomination.judge_id.in_(contact_family(judge.id)),
        ).order_by((PilotNomination.judge_id == judge.id).desc(), PilotNomination.id).limit(1))
        if duplicate and (not nomination or duplicate.id != nomination.id) and duplicate.review_status is not None:
            fail("already_requested", "This judge already has a request for this competition.", 409)
        if duplicate and nomination and duplicate.id != nomination.id:
            fail("already_nominated", "This judge already has a nomination for the destination competition.", 409)
    else:
        duplicate = None

    if nomination or not judge:
        first, _, last = name.partition(" ")
        values = dict(display_name=name, first_name=first[:200], last_name=last[:200],
                      primary_email=address, phone=clean(data.phone))
        if judge and judge.primary_email and judge.primary_email != address:
            judge.preserved_emails = sorted(set(judge.preserved_emails) | {judge.primary_email})
        judge = write_judge(db, values, user, existing=judge, source="judge recruitment")
    else:
        details = {}
        if not judge.primary_email and not judge.alternate_email:
            details["primary_email"] = address
        if data.phone and not judge.phone:
            details["phone"] = data.phone
        if details:
            write_judge(db, details, user, existing=judge)

    record = nomination or duplicate or PilotNomination(pilot_id=competition.id, judge_id=judge.id)
    before = serialize(record) if record.id else None
    record.pilot_id = competition.id
    record.judge_id = judge.id
    record.qualification_text = qualification
    record.request_notes = clean(data.notes)
    record.prior_involvement = clean(data.prior_involvement)
    if not nomination:
        record.review_status = "pending"
        record.requested_by = user.id
    if nomination:
        record.updated_at = now()
    db.add(record)
    db.flush()
    audit(db, user, "recruitment.edited" if nomination else "recruitment.requested", record,
          "Edited judge request" if nomination else "Submitted judge request", before, serialize(record))
    return record, competition, judge


def notify_request(db, record, competition, judge, user):
    notify(db, "judge_request", f"Judge request: {judge.display_name}",
           f"{user.full_name} requested a judge for {competition.name}.", recruitment_href(competition, record),
           judge_id=judge.id)


def queue_nomination(db, competition, judge, user, qualification=""):
    """Submit a request from the existing competition contact entry points."""
    ensure_contact_unambiguous(db, judge)
    if db.scalar(select(tournament_archive(judge.id, competition.event_year))):
        fail("candidate_archived", "Reopen the archived request before recruiting this judge for this tournament.", 409)
    record = PilotNomination(pilot_id=competition.id, judge_id=judge.id,
                             qualification_text=qualification, requested_by=user.id, review_status="pending")
    db.add(record)
    audit(db, user, "recruitment.requested", record, "Submitted judge request")
    notify_request(db, record, competition, judge, user)
    return record


@router.post("")
def submit_request(data: RecruitmentIn, user=Depends(require("judges")), db=Depends(get_db)):
    lock_assignments(db)
    record, competition, judge = write_request(db, data, user)
    notify_request(db, record, competition, judge, user)
    db.commit()
    return serialize(record)


@router.patch("/{id}")
def edit_request(id: UUID, data: RecruitmentIn, user=Depends(require("recruitment_review")), db=Depends(get_db)):
    lock_assignments(db)
    record = get(db, PilotNomination, id, True)
    if record.review_status != "pending":
        fail("review_closed", "Only a pending request can be edited during review.", 409)
    if get(db, PilotCompetition, record.pilot_id).event_year != target_competition(db, data.competition_id).event_year:
        fail("wrong_tournament", "Review edits must stay within the request's tournament.", 409)
    if record.pilot_id != data.competition_id and (
        record.status != "to_contact" or record.responded_at or record.last_contacted_at or record.reopened_at
    ):
        fail("request_has_history", "Create a separate request for the new competition to preserve the original response.", 409)
    record, competition, _ = write_request(db, data, user, record)
    db.execute(update(Notification).where(Notification.kind == "judge_request",
               Notification.href.endswith(f"&candidate={record.id}")).values(href=recruitment_href(competition, record)))
    db.commit()
    return serialize(record)


@router.post("/{id}/review")
def review_request(id: UUID, data: RecruitmentReview, user=Depends(require("recruitment_review")), db=Depends(get_db)):
    lock_assignments(db)
    record = get(db, PilotNomination, id, True)
    if record.review_status != "pending" or data.version != record.updated_at:
        fail("request_changed", "The request changed. Refresh and review the latest details.", 409)
    competition = get(db, PilotCompetition, record.pilot_id)
    if data.decision == "approved":
        target_competition(db, competition.id)
        judge = get(db, Judge, record.judge_id)
        if judge.merged_into_id or not record.qualification_text.strip():
            fail("request_incomplete", "An active contact and qualifications are required before approval.", 409)
        contact_email(judge.primary_email or judge.alternate_email)
        if db.scalar(select(tournament_archive(judge.id, competition.event_year))):
            fail("candidate_archived", "Reopen the archived request before approving this judge for this tournament.", 409)
    before = serialize(record)
    record.review_status, record.reviewed_at, record.reviewed_by = data.decision, now(), user.id
    audit(db, user, f"recruitment.{data.decision}", record, f"Judge request {data.decision}", before, serialize(record))
    href = recruitment_href(competition, record)
    db.execute(update(Notification).where(Notification.kind == "judge_request", Notification.href == href).values(read_at=now()))
    notify(db, "judge_reviewed", f"Judge request {data.decision}",
           f"{get(db, Judge, record.judge_id).display_name} · {competition.name}", href,
           judge_id=record.judge_id, recipient_user_id=record.requested_by)
    db.commit()
    return serialize(record)


def update_response(db, record, status, user):
    competition = target_competition(db, record.pilot_id)
    judge = get(db, Judge, record.judge_id)
    if judge.merged_into_id:
        fail("invalid_judge", "Use a request for the surviving contact before changing this response.", 409)
    if record.review_status not in {None, "approved"}:
        fail("approval_required", "The chair must approve this request before recording an outreach response.", 409)
    assignment_scope = (
        OfficialJudgeAssignment.event_year == competition.event_year
        if status == "declined" else OfficialJudgeAssignment.competition_id == competition.id
    )
    if status != "accepted" and db.scalar(live_assignments().where(
        OfficialJudgeAssignment.judge_id == record.judge_id,
        assignment_scope,
    ).limit(1)):
        fail("judge_assigned", "Use Unconfirm to record a confirmed judge's withdrawal.", 409)
    if db.scalar(select(tournament_archive(judge.id, competition.event_year))):
        fail("candidate_archived", "Reopen the archived request before updating a response for this tournament.", 409)
    before = serialize(record)
    record.responded_at = now() if status in {"accepted", "declined"} else None
    if status == "awaiting_response" and record.status != status:
        record.last_contacted_at = now()
    record.status = status
    audit(db, user, "pilot.response_updated", record, f"Judge response: {status}", before, serialize(record))


@router.post("/{id}/response")
def record_response(id: UUID, data: PilotStatusIn, user=Depends(require("judges")), db=Depends(get_db)):
    lock_assignments(db)
    record = get(db, PilotNomination, id, True)
    target_competition(db, record.pilot_id)
    update_response(db, record, data.status, user)
    db.commit()
    return serialize(record)


@router.post("/{id}/confirm")
def confirm_request(id: UUID, user=Depends(require("judges")), db=Depends(get_db)):
    lock_assignments(db)
    record = get(db, PilotNomination, id, True)
    competition = target_competition(db, record.pilot_id)
    if record.review_status != "approved":
        fail("approval_required", "An approved request is required to confirm this judge.", 409)
    occupied = set(db.scalars(live_assignments().with_only_columns(OfficialJudgeAssignment.slot_number)
                              .where(OfficialJudgeAssignment.competition_id == competition.id)))
    slot = next((number for number in range(1, REQUIRED_JUDGES + 1) if number not in occupied), None)
    if slot is None:
        fail("competition_full", f"{competition.name} already has three confirmed judges.", 409)
    assignment = place_assignment(db, competition, OfficialAssignmentIn(nomination_id=id, slot_number=slot), user)
    db.commit()
    return serialize(assignment)


@router.post("/assignments/{id}/withdraw")
def withdraw(id: UUID, user=Depends(require("judges")), db=Depends(get_db)):
    lock_assignments(db)
    assignment = db.scalar(live_assignments().where(OfficialJudgeAssignment.id == id))
    if not assignment:
        fail("assignment_changed", "This assignment is no longer active. Refresh the directory.", 409)
    judge = db.get(Judge, assignment.judge_id)
    if not db.scalar(select(PilotNomination.id).join(Judge, Judge.id == PilotNomination.judge_id).where(
        PilotNomination.pilot_id == assignment.competition_id,
        or_(Judge.id == judge.id, Judge.merged_into_id == judge.id),
    ).limit(1)):
        db.add(PilotNomination(pilot_id=assignment.competition_id, judge_id=judge.id, status="accepted"))
    release_assignment(db, assignment, user, "withdrawn")
    db.commit()
    return serialize(assignment)


@router.post("/{id}/reopen")
def reopen_request(id: UUID, user=Depends(require("judges")), db=Depends(get_db)):
    lock_assignments(db)
    record = get(db, PilotNomination, id, True)
    competition = get(db, PilotCompetition, record.pilot_id)
    judge = get(db, Judge, record.judge_id)
    if judge.merged_into_id:
        judge = get(db, Judge, judge.merged_into_id)
    withdrawn = db.scalar(outstanding_withdrawals(judge.id).where(
        OfficialJudgeAssignment.competition_id == competition.id,
    ).limit(1))
    original_disposition = withdrawn or record.status == "declined"
    active_competition = competition.deleted_at is None and competition.activation_status == "active"
    if not active_competition and not original_disposition:
        fail("competition_inactive", "Choose an active competition.", 409)
    archived = original_disposition or record.review_status == "rejected"
    if not original_disposition and db.scalar(select(tournament_archive(judge.id, competition.event_year))):
        fail("candidate_archived", "Reopen the original withdrawal or decline before returning this request to Prospect.", 409)
    if not archived and record.review_status != "approved":
        fail("request_not_reopenable", "This request is already in the Prospect stage.", 409)
    if not archived and not allowed(user, "recruitment_review"):
        fail("forbidden", "Only a chair or director can return an approved request to Prospect.", 403)
    if db.scalar(live_assignments().where(
        OfficialJudgeAssignment.judge_id == judge.id,
        OfficialJudgeAssignment.event_year == competition.event_year,
    ).limit(1)):
        fail("judge_assigned", "Release the official assignment before reopening recruitment.", 409)
    before = serialize(record)
    record.review_status = "pending"
    record.reviewed_at = record.reviewed_by = None
    record.status = "to_contact"
    record.responded_at = record.last_contacted_at = None
    record.reopened_at = now()
    record.requested_by = record.requested_by or user.id
    existing_request = False
    if record.judge_id != judge.id:
        existing_request = db.scalar(select(PilotNomination.id).where(
            PilotNomination.pilot_id == competition.id, PilotNomination.judge_id == judge.id,
        )) is not None
        if existing_request and not original_disposition:
            fail("request_merge_conflict", "The surviving contact already has a request for this competition.", 409)
        if not existing_request:
            record.judge_id = judge.id
    resumes_request = active_competition and not existing_request
    summary = "Moved judge request back to Prospect" if resumes_request else "Lifted tournament archive on a historical request"
    audit(db, user, "recruitment.reopened", record, summary, before, serialize(record))
    if resumes_request:
        notify_request(db, record, competition, judge, user)
    db.commit()
    return serialize(record)


def save_judge_details(db, record, data, user):
    qualification, notes = clean(data.qualifications).strip(), clean(data.notes).strip()
    changed = [label for label, previous, current in (
        ("qualifications", record.qualification_text, qualification),
        ("notes", record.request_notes, notes),
    ) if previous != current]
    if changed:
        before = serialize(record)
        record.qualification_text, record.request_notes = qualification, notes
        audit(db, user, "recruitment.details_updated", record, f"Updated {' and '.join(changed)}",
              before, serialize(record))


@router.patch("/{id}/details")
def edit_candidate_details(id: UUID, data: JudgeDetailsIn, user=Depends(require("judges")), db=Depends(get_db)):
    lock_assignments(db)
    record = get(db, PilotNomination, id, True)
    save_judge_details(db, record, data, user)
    db.commit()
    return serialize(record)


def recruitment_rows(event_year):
    assigned = select(OfficialJudgeAssignment.id).where(
        OfficialJudgeAssignment.competition_id == PilotCompetition.id,
        OfficialJudgeAssignment.judge_id == Judge.id, OfficialJudgeAssignment.released_at.is_(None),
    ).correlate(PilotCompetition, Judge).exists()
    disposition = tournament_archive(Judge.id, PilotCompetition.event_year)
    approved = PilotNomination.review_status == "approved"
    stage = case((assigned, "confirmed"), (and_(approved, PilotNomination.last_contacted_at.is_not(None)), "contacted"),
                 (approved, "approved"), else_="prospect")
    archive = case((assigned, ""), (disposition != "", disposition),
                   (PilotCompetition.deleted_at.is_not(None), "inactive"),
                   (PilotCompetition.activation_status != "active", "inactive"),
                   (PilotNomination.review_status == "rejected", "rejected"), else_="")
    submitter = aliased(User)
    original_contact = aliased(Judge)
    canonical_request = aliased(PilotNomination)
    has_canonical_request = select(canonical_request.id).where(
        canonical_request.judge_id == Judge.id, canonical_request.pilot_id == PilotNomination.pilot_id,
    ).correlate(Judge, PilotNomination).exists()
    returning = select(Engagement.id).where(Engagement.judge_id == Judge.id,
        Engagement.event_year < event_year).correlate(Judge).exists()
    previously_assigned = select(OfficialJudgeAssignment.id).where(OfficialJudgeAssignment.judge_id.in_(contact_family(Judge.id)),
        OfficialJudgeAssignment.event_year < event_year).correlate(Judge).exists()
    statement = select(PilotNomination, PilotCompetition, Judge, submitter.full_name, stage, archive,
                       returning | previously_assigned).select_from(PilotNomination).join(PilotCompetition, PilotNomination.pilot_id == PilotCompetition.id
        ).join(original_contact, PilotNomination.judge_id == original_contact.id
        ).join(Judge, Judge.id == func.coalesce(original_contact.merged_into_id, original_contact.id)
        ).outerjoin(submitter, PilotNomination.requested_by == submitter.id
        ).where(PilotCompetition.event_year == event_year, or_(
            original_contact.merged_into_id.is_(None), ~has_canonical_request, PilotNomination.status == "declined",
        ))
    return statement, stage, archive, submitter


def candidate_view(row):
    record, competition, judge, submitter, stage, archive, returning = row
    return {**serialize(record), "judge_id": str(judge.id), "name": judge.display_name, "email": judge.primary_email or judge.alternate_email,
            "phone": judge.phone, "organization": judge.organization, "contact_notes": judge.notes,
            "competition": competition.name, "category": CATEGORIES[competition.category_slug][0],
            "event_year": competition.event_year, "category_slug": competition.category_slug,
            "submitted_by": submitter or "Existing nomination", "stage": stage, "archive_status": archive,
            "returning": returning or bool(record.prior_involvement), "do_not_contact": judge.do_not_contact}


@router.get("", response_model=Page)
def candidates(
    event_year: int = Query(ge=2000, le=2200), q: str = "", competition_id: UUID | None = None, category_slug: str = "",
    view: str = "pipeline", stage_filter: str = Query("", alias="stage"), sort: str = "updated",
    direction: str = "desc", page_no: int = Query(1, alias="page", ge=1),
    page_size: int = Query(5, ge=1, le=100), user=Depends(require("judges")), db=Depends(get_db),
):
    stmt, stage, archive, submitter = recruitment_rows(event_year)
    scope = stage != "confirmed"
    filters = {
        "pipeline": and_(scope, archive == ""),
        "requests": and_(scope, archive == "", PilotNomination.review_status == "pending"),
        "outreach": and_(scope, archive == "", PilotNomination.review_status == "approved"),
        "notes": and_(scope, archive == "", PilotNomination.request_notes != ""),
        "archive": archive != "",
    }
    if view not in filters:
        fail("invalid_view", "Choose a recruitment view.")
    stmt = stmt.where(filters[view])
    if competition_id:
        stmt = stmt.where(PilotCompetition.id == competition_id)
    if category_slug:
        stmt = stmt.where(PilotCompetition.category_slug == category_slug)
    if q.strip():
        term = f"%{q.strip()}%"
        stmt = stmt.where(or_(Judge.display_name.ilike(term), Judge.primary_email.ilike(term),
                              Judge.alternate_email.ilike(term), Judge.phone.ilike(term)))
    if stage_filter:
        stmt = stmt.where(stage == ("approved" if stage_filter == "vetted" else stage_filter))
    total = db.scalar(select(func.count()).select_from(stmt.subquery()))
    column = {"name": Judge.display_name, "submitted": submitter.full_name}.get(sort, PilotNomination.updated_at)
    order = column.asc() if direction == "asc" else column.desc()
    rows = db.execute(stmt.order_by(order, PilotNomination.id).offset((page_no - 1) * page_size).limit(page_size))
    return {"items": [candidate_view(row) for row in rows], "total": total, "page": page_no, "page_size": page_size}


@router.get("/summary")
def summary(event_year: int = Query(ge=2000, le=2200), user=Depends(require("judges")), db=Depends(get_db)):
    rows, stage, archive, _ = recruitment_rows(event_year)
    active = rows.where(stage != "confirmed", archive == "")
    stages = dict(db.execute(active.with_only_columns(stage, func.count()).group_by(stage)).all())
    requests = db.scalar(active.with_only_columns(func.count()).where(PilotNomination.review_status == "pending"))
    confirmed = db.scalar(live_assignments().with_only_columns(func.count(), maintain_column_froms=True).join(
        PilotCompetition, PilotCompetition.id == OfficialJudgeAssignment.competition_id).where(
        OfficialJudgeAssignment.event_year == event_year, PilotCompetition.deleted_at.is_(None),
        PilotCompetition.activation_status == "active"))
    return {"total": sum(stages.values()), "requests": requests, "contacted": stages.get("contacted", 0),
            "confirmed": confirmed, "stages": {**stages, "confirmed": confirmed}}


@router.get("/{id}")
def candidate(id: UUID, user=Depends(require("judges")), db=Depends(get_db)):
    record = get(db, PilotNomination, id)
    rows, *_ = recruitment_rows(get(db, PilotCompetition, record.pilot_id).event_year)
    row = db.execute(rows.where(PilotNomination.id == id)).one_or_none()
    if not row:
        fail("not_found", "This contact was merged. Open the surviving contact.", 404)
    judge = row[2]
    assignment_ids = select(cast(OfficialJudgeAssignment.id, String)).where(
        OfficialJudgeAssignment.competition_id == record.pilot_id,
        OfficialJudgeAssignment.judge_id.in_(contact_family(judge.id)),
    )
    archive_status = row[5]
    can_reopen_archive = record.status == "declined" or (
        archive_status == "withdrawn" and db.scalar(outstanding_withdrawals(judge.id).where(
            OfficialJudgeAssignment.competition_id == record.pilot_id,
        ).limit(1)) is not None
    ) or archive_status == "rejected"
    history = db.execute(select(Audit, User.full_name).outerjoin(User, Audit.actor_user_id == User.id)
        .where(or_(
            and_(Audit.entity_type == "pilot_nominations", Audit.entity_id == str(id)),
            and_(Audit.entity_type == "official_judge_assignments", Audit.entity_id.in_(assignment_ids)),
        ))
        .order_by(Audit.created_at.desc()).limit(50))
    competition = row[1]
    duplicate_request = record.judge_id != judge.id and db.scalar(select(PilotNomination.id).where(
        PilotNomination.pilot_id == competition.id, PilotNomination.judge_id == judge.id,
    )) is not None
    reopen_returns_to_prospect = competition.deleted_at is None and competition.activation_status == "active" and not duplicate_request
    return {**candidate_view(row), "can_reopen_archive": can_reopen_archive, "reopen_returns_to_prospect": reopen_returns_to_prospect, "history": [
        {"id": str(entry.id), "summary": entry.summary, "created_at": entry.created_at, "actor": actor}
        for entry, actor in history
    ]}


def merge_recruitment_contacts(db, survivor, other, actor):
    """Keep managed requests visible after a merge without dropping either request."""
    records = db.execute(select(PilotNomination, PilotCompetition).join(
        PilotCompetition, PilotCompetition.id == PilotNomination.pilot_id,
    ).where(
        PilotNomination.judge_id.in_([survivor.id, other.id]),
    )).all()
    for request, competition in records:
        if (request.review_status not in {"pending", "approved"} or request.status == "declined"
                or competition.deleted_at or competition.activation_status != "active"):
            continue
        owner_id = request.judge_id
        opposite_id = other.id if owner_id == survivor.id else survivor.id
        year = competition.event_year
        if (not db.scalar(select(tournament_archive(owner_id, year)))
                and db.scalar(select(tournament_archive(opposite_id, year)))):
            fail("request_merge_conflict",
                 f"The merge would archive an active request in {year}. Resolve the archived or active request before merging.", 409)
    rows = [request for request, _ in records]
    surviving = {row.pilot_id: row for row in rows if row.judge_id == survivor.id}
    for row in rows:
        if row.judge_id != other.id or row.review_status is None:
            continue
        if row.pilot_id in surviving:
            fail("request_merge_conflict",
                 "Both contacts have records in the same competition. The merge would hide a recruitment request.", 409)
        before = serialize(row)
        row.judge_id = survivor.id
        audit(db, actor, "recruitment.contact_merged", row, "Linked the request to the surviving contact", before, serialize(row))


def outreach_block(db, competition, address, judge_id=None, allow_declined=False, event_year=None, *, check_identity=True):
    """Validate managed requests even when a recipient was entered as a raw email."""
    if competition and (competition.deleted_at or competition.activation_status != "active"):
        return "Competition is inactive"
    contacts = email_contacts(address).with_only_columns(Judge.id)
    ids = set(db.scalars(contacts))
    if judge_id:
        ids.add(judge_id)
    if not ids:
        return ""
    if check_identity and any(contact_has_email_conflict(db, judge, destination=address) for judge in db.scalars(
        select(Judge).where(Judge.id.in_(ids), Judge.merged_into_id.is_(None)),
    )):
        return "Resolve contacts that share an email address before continuing"
    stmt = select(PilotNomination, PilotCompetition).join(PilotCompetition).where(
        PilotNomination.judge_id.in_(ids), PilotCompetition.deleted_at.is_(None))
    if competition:
        stmt = stmt.where(PilotCompetition.id == competition.id)
    else:
        stmt = stmt.where(PilotNomination.review_status.is_not(None))
        if event_year is not None:
            stmt = stmt.where(PilotCompetition.event_year == event_year)
    rows = db.execute(stmt).all()
    if not competition and event_year is not None and not rows:
        if db.scalar(select(PilotNomination.id).where(
            PilotNomination.judge_id.in_(ids), PilotNomination.review_status.in_(["pending", "rejected"]),
        ).limit(1)) and not db.scalar(live_assignments().where(
            OfficialJudgeAssignment.judge_id.in_(ids), OfficialJudgeAssignment.event_year == event_year,
        ).limit(1)):
            return "Choose the competition and approved judge request before outreach"
    year = competition.event_year if competition else event_year
    for contact_id in ids:
        withdrawals = outstanding_withdrawals(contact_id)
        if year:
            withdrawals = withdrawals.where(OfficialJudgeAssignment.event_year == year)
        if db.scalar(withdrawals.limit(1)):
            return "Judge withdrew from this tournament"
    family_ids = select(Judge.id).where(or_(Judge.id.in_(ids), Judge.merged_into_id.in_(ids)))
    declines = select(PilotNomination.id).join(PilotCompetition).where(
        PilotNomination.judge_id.in_(family_ids), PilotNomination.status == "declined")
    if year:
        declines = declines.where(PilotCompetition.event_year == year)
    is_manual_decline = allow_declined and competition and any(
        record.review_status == "approved" and record.status == "declined" for record, _ in rows)
    if not is_manual_decline and db.scalar(declines.limit(1)):
        return "Judge declined outreach for this tournament"
    if competition and not rows and db.scalar(select(PilotNomination.id).join(PilotCompetition).where(
        PilotNomination.judge_id.in_(ids), PilotCompetition.event_year == competition.event_year,
        PilotNomination.review_status.is_not(None)).limit(1)):
        return "No approved request for this competition"
    for nomination, target in rows:
        if nomination.review_status in {"pending", "rejected"}:
            return "Chair approval required for this judge request"
        if nomination.status == "declined" and not (allow_declined and competition and nomination.review_status == "approved"):
            return "Judge declined this competition"
    return ""
