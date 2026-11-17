"""Transactions for official judge slots and their history."""
from sqlalchemy import case, func, or_, select
from sqlalchemy.orm import aliased

from .auth import fail
from .db import now
from .models import Judge, OfficialJudgeAssignment, PilotCompetition, PilotNomination
from .services import audit, email_address_matches, judge_email_addresses, lock_contact_writes, serialize

REQUIRED_JUDGES = 3


def lock_assignments(db):
    # Confirmations, contact merges, directory clearing, and pilot deletion share
    # this transaction lock. The slot and tournament indexes also enforce capacity.
    db.execute(select(func.pg_advisory_xact_lock(410262)))


def live_assignments():
    return select(OfficialJudgeAssignment).where(OfficialJudgeAssignment.released_at.is_(None))


def contact_family(judge_id):
    contact = aliased(Judge)
    return select(contact.id).where(or_(contact.id == judge_id, contact.merged_into_id == judge_id)).correlate(Judge)


def outstanding_withdrawals(judge_id):
    """A withdrawal stays in effect until the corresponding nomination is reopened."""
    contact = aliased(Judge)
    reopened = aliased(PilotNomination)
    reopened_since = select(reopened.id).join(contact, contact.id == reopened.judge_id).where(
        reopened.pilot_id == OfficialJudgeAssignment.competition_id,
        reopened.reopened_at > OfficialJudgeAssignment.released_at,
        func.coalesce(contact.merged_into_id, contact.id) == judge_id,
    ).correlate(OfficialJudgeAssignment, Judge).exists()
    return select(OfficialJudgeAssignment.id).where(
        OfficialJudgeAssignment.judge_id.in_(contact_family(judge_id)),
        OfficialJudgeAssignment.release_reason == "withdrawn",
        ~reopened_since,
    )


def tournament_archive(judge_id, event_year):
    withdrawal = outstanding_withdrawals(judge_id).where(
        OfficialJudgeAssignment.event_year == event_year,
    ).correlate(PilotCompetition, Judge).exists()
    nomination, competition = aliased(PilotNomination), aliased(PilotCompetition)
    decline = select(nomination.id).join(competition, competition.id == nomination.pilot_id).where(
        nomination.judge_id.in_(contact_family(judge_id)), nomination.status == "declined",
        competition.event_year == event_year,
    ).correlate(PilotCompetition, Judge).exists()
    return case((withdrawal, "withdrawn"), (decline, "declined"), else_="")


def contact_has_email_conflict(db, judge, destination=None):
    addresses = judge_email_addresses(judge)
    conflicts = select(Judge.id).where(
        Judge.id != judge.id, Judge.merged_into_id.is_(None), email_address_matches(addresses),
    )
    if destination:
        # The caller already checks all records matching the exact destination.
        conflicts = conflicts.where(~email_address_matches([destination]))
    return bool(addresses and db.scalar(conflicts.limit(1)))


def ensure_contact_unambiguous(db, judge):
    if contact_has_email_conflict(db, judge):
        fail("contact_conflict", "Resolve contacts that share an email address before continuing.", 409)


def release_assignment(db, assignment, actor, reason):
    before = serialize(assignment)
    assignment.released_at = now()
    assignment.released_by = actor.id
    assignment.release_reason = reason
    audit(db, actor, "judge_assignment.released", assignment,
          f"Released slot {assignment.slot_number} ({reason.replace('_', ' ')})",
          before, serialize(assignment))


def place_assignment(db, competition, data, actor):
    lock_contact_writes(db)
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
    if db.scalar(select(tournament_archive(nomination.judge_id, competition.event_year))):
        fail("candidate_archived", "Reopen the withdrawn or declined request before confirming this judge for this tournament.", 409)
    if nomination.status != "accepted":
        fail("acceptance_required", "Record an Accepted response before confirming this judge.", 409)
    judge = db.get(Judge, nomination.judge_id)
    if not judge or judge.merged_into_id:
        fail("invalid_judge", "Use the active judge contact before confirming an assignment.", 409)
    ensure_contact_unambiguous(db, judge)

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
    for row in rows:
        other_contact = other.id if row.judge_id == survivor.id else survivor.id
        if db.scalar(select(tournament_archive(other_contact, row.event_year))):
            fail("assignment_merge_conflict", "One contact holds an official slot in a tournament where the other is archived. Resolve the archive or slot before merging.", 409)
    for row in moving:
        before = serialize(row)
        row.judge_id = survivor.id
        audit(db, actor, "judge_assignment.contact_merged", row, "Linked the official slot to the surviving contact",
              before, serialize(row))
