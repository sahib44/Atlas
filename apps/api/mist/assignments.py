"""Transactions for official judge slots and their history."""
from sqlalchemy import func, or_, select
from sqlalchemy.orm import aliased

from .auth import fail
from .db import now
from .models import Judge, OfficialJudgeAssignment, PilotCompetition, PilotNomination
from .services import audit, serialize

REQUIRED_JUDGES = 3


def lock_assignments(db):
    # Confirmations, contact merges, directory clearing, and pilot deletion share
    # this transaction lock. The slot and tournament indexes also enforce capacity.
    db.execute(select(func.pg_advisory_xact_lock(410262)))


def live_assignments():
    return select(OfficialJudgeAssignment).where(OfficialJudgeAssignment.released_at.is_(None))


def outstanding_withdrawals(judge_id):
    """A withdrawal stays in effect until the corresponding nomination is reopened."""
    contact = aliased(Judge)
    reopened = aliased(PilotNomination)
    reopened_since = select(reopened.id).join(contact, contact.id == reopened.judge_id).where(
        reopened.pilot_id == OfficialJudgeAssignment.competition_id,
        reopened.reopened_at > OfficialJudgeAssignment.released_at,
        func.coalesce(contact.merged_into_id, contact.id) == judge_id,
    ).correlate(OfficialJudgeAssignment, Judge).exists()
    former = aliased(Judge)
    contacts = select(former.id).where(or_(former.id == judge_id, former.merged_into_id == judge_id)).correlate(Judge)
    return select(OfficialJudgeAssignment.id).where(
        OfficialJudgeAssignment.judge_id.in_(contacts),
        OfficialJudgeAssignment.release_reason == "withdrawn",
        ~reopened_since,
    )


def release_assignment(db, assignment, actor, reason):
    before = serialize(assignment)
    assignment.released_at = now()
    assignment.released_by = actor.id
    assignment.release_reason = reason
    audit(db, actor, "judge_assignment.released", assignment,
          f"Released slot {assignment.slot_number} ({reason.replace('_', ' ')})",
          before, serialize(assignment))


def place_assignment(db, competition, data, actor):
    if competition.activation_status != "active":
        fail("competition_inactive", "Only an active competition can confirm judges.", 409)

    nomination = db.scalar(select(PilotNomination).where(
        PilotNomination.id == data.nomination_id,
        PilotNomination.pilot_id == competition.id,
    ).with_for_update())
    if not nomination:
        fail("nomination_not_found", "Choose a judge nominated for this competition.", 404)
    if nomination.review_status not in {None, "approved"}:
        fail("approval_required", "The chair must approve this request before confirmation.", 409)
    if db.scalar(outstanding_withdrawals(nomination.judge_id).where(
        OfficialJudgeAssignment.competition_id == competition.id,
    ).limit(1)):
        fail("candidate_archived", "This judge withdrew from this competition.", 409)
    if nomination.status != "accepted":
        fail("acceptance_required", "Record an Accepted response before confirming this judge.", 409)
    judge = db.get(Judge, nomination.judge_id)
    if not judge or judge.merged_into_id:
        fail("invalid_judge", "Use the active judge contact before confirming an assignment.", 409)

    current = db.scalar(live_assignments().where(
        OfficialJudgeAssignment.event_year == competition.event_year,
        OfficialJudgeAssignment.judge_id == judge.id,
    ))
    if data.previous_assignment_id:
        if not current or current.id != data.previous_assignment_id:
            fail("assignment_changed", "The current assignment changed. Refresh and review it before moving the judge.", 409)
    elif current:
        source = db.get(PilotCompetition, current.competition_id)
        fail("judge_already_assigned", f"This judge holds an official slot in {source.name} for {competition.event_year}. Reassign them explicitly to move the slot.", 409)

    occupied = db.scalar(live_assignments().where(
        OfficialJudgeAssignment.competition_id == competition.id,
        OfficialJudgeAssignment.slot_number == data.slot_number,
    ))
    if occupied:
        fail("slot_occupied", "This slot is already occupied. Choose a vacant slot.", 409)

    if current:
        release_assignment(db, current, actor, "reassigned")
        db.flush()

    assignment = OfficialJudgeAssignment(
        competition_id=competition.id, event_year=competition.event_year,
        slot_number=data.slot_number, judge_id=judge.id, judge_name=judge.display_name,
        confirmed_by=actor.id,
    )
    db.add(assignment)
    db.flush()
    audit(db, actor, "judge_assignment.reassigned" if current else "judge_assignment.confirmed",
          assignment, f"{competition.name} · official slot {assignment.slot_number}",
          after={**serialize(assignment), "previous_assignment_id": str(current.id) if current else None})
    return assignment


def merge_assignment_contacts(db, survivor, other, actor):
    """Keep live slots on the surviving contact when there is no year conflict."""
    rows = list(db.scalars(live_assignments().where(
        OfficialJudgeAssignment.judge_id.in_([survivor.id, other.id]),
    )))
    occupied_years = {row.event_year for row in rows if row.judge_id == survivor.id}
    moving = [row for row in rows if row.judge_id == other.id]
    conflicts = sorted({row.event_year for row in moving} & occupied_years)
    if conflicts:
        years = ", ".join(map(str, conflicts))
        fail("assignment_merge_conflict", f"Both contacts hold official slots in {years}. Resolve the assignments before merging.", 409)
    for row in moving:
        before = serialize(row)
        row.judge_id = survivor.id
        audit(db, actor, "judge_assignment.contact_merged", row, "Linked the official slot to the surviving contact",
              before, serialize(row))
