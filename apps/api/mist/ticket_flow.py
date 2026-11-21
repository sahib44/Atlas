"""Atomic ticket changes and immutable, chair-reviewed reply snapshots."""
import hashlib
import json
from datetime import timedelta
from uuid import UUID, uuid4
from sqlalchemy import func, select, update
from .auth import allowed, fail
from .db import now
from .models import Audit, Ticket, TicketMessage, TicketNote, TicketDraft, TicketAction, Notification, Outbound, User
from .schemas import ForwardIn
from .services import audit, clean, contact_email, email, serialize

STATUSES = {"new", "in_progress", "waiting_on_organizer", "waiting_on_requester", "resolved", "closed"}
TRANSITIONS = {
    "new": {"in_progress", "waiting_on_organizer", "waiting_on_requester", "resolved"},
    "in_progress": {"waiting_on_organizer", "waiting_on_requester", "resolved"},
    "waiting_on_organizer": {"in_progress", "waiting_on_requester", "resolved"},
    "waiting_on_requester": {"in_progress", "waiting_on_organizer", "resolved"},
    "resolved": {"closed", "in_progress", "waiting_on_organizer", "waiting_on_requester"},
    "closed": {"in_progress", "waiting_on_organizer"},
}


def text_value(value):
    return clean(value).strip()


def reply_subject_prefix(ticket):
    return f"Re: {ticket.public_number} — "


def default_reply_subject(ticket):
    subject = " ".join(ticket.subject.splitlines()).strip() or "Competition ticket"
    return reply_subject_prefix(ticket) + subject


def reply_subject_value(ticket, value):
    if value is None:
        return default_reply_subject(ticket)
    subject = text_value(value)
    if not subject or value.splitlines() != [value]:
        fail("invalid_subject", "Enter a reply subject on one line.", 422)
    prefix = reply_subject_prefix(ticket)
    if not subject.startswith(prefix):
        subject = prefix + subject
    if len(subject) > 1000:
        fail("invalid_subject", "Enter a reply subject on one line with at most 1,000 characters.", 422)
    return subject


def draft_reply_subject(draft, ticket):
    return draft.reply_subject or default_reply_subject(ticket)


def can_approve_public_reuse(draft):
    return draft.kind == "notes" and bool(draft.body) and not draft.target_status and not draft.resolution_note


def approval_matches(draft, ticket, kind, body, target_status, resolution_note, subject):
    if draft.body != body or (subject is not None and draft_reply_subject(draft, ticket) != subject):
        return False
    if draft.kind == kind:
        return draft.target_status == target_status and draft.resolution_note == resolution_note
    return bool(
        kind == "messages" and target_status == "waiting_on_requester" and not resolution_note
        and draft.public_reply_approved and can_approve_public_reuse(draft)
    )


class ForwardRequest(ForwardIn):
    request_id: UUID | None = None


def forward_request(db, route, actor_id, request_id, source_id, address, note):
    if request_id is None:
        return f"{route}:{uuid4()}", None

    identity = hashlib.sha256(f"{route}:{actor_id}:{request_id}".encode()).digest()
    # Serialize reuse of the same request, including attempts against different source messages.
    db.execute(select(func.pg_advisory_xact_lock(int.from_bytes(identity[:8], "big", signed=True))))
    prefix = f"{route}:{identity[:16].hex()}:"
    # Keep the key short enough for the mock delivery receipt, which includes it.
    intent = hashlib.sha256(json.dumps([str(source_id), address, note]).encode()).digest()
    key = prefix + intent[:16].hex()
    existing = db.scalar(select(Outbound).where(Outbound.idempotency_key.startswith(prefix)))
    if existing and existing.idempotency_key != key:
        fail("request_reused", "This forward ID was already used for a different message.", 409)
    return key, existing


def supersede_drafts(db, ticket, author_id=None, kind=None, *, preserve_public=False):
    stmt = select(TicketDraft).where(TicketDraft.ticket_id == ticket.id, TicketDraft.status.in_(["pending_approval", "approved"]))
    if author_id:
        stmt = stmt.where(TicketDraft.created_by == author_id)
    if kind:
        stmt = stmt.where(TicketDraft.kind == kind)
    if preserve_public:
        stmt = stmt.where(~(
            TicketDraft.public_reply_approved & (TicketDraft.status == "approved")
            & (TicketDraft.base_version == ticket.version)
        ))
    ids = []
    for draft in db.scalars(stmt):
        draft.status = "superseded"
        ids.append(draft.id)
    if ids:
        db.execute(update(Notification).where(Notification.draft_id.in_(ids)).values(read_at=now()))


def changed(db, ticket, *, private_note_only=False):
    if private_note_only:
        # Private notes change no part of the explicitly approved public envelope.
        # Other approvals still need review after the ticket version advances.
        db.execute(update(TicketDraft).where(
            TicketDraft.ticket_id == ticket.id,
            TicketDraft.public_reply_approved,
            TicketDraft.status == "approved",
            TicketDraft.base_version == ticket.version,
        ).values(base_version=ticket.version + 1))
    ticket.version += 1
    supersede_drafts(db, ticket, preserve_public=private_note_only)


def mark_ticket_notifications(db, ticket):
    if ticket.status != "new":
        db.execute(update(Notification).where(Notification.ticket_id == ticket.id, Notification.kind == "ticket", Notification.read_at.is_(None)).values(read_at=now()))


def ticket_approver(db, user_id, *, lock=False):
    if lock:
        approver = db.scalar(
            select(User).where(User.id == user_id).with_for_update(read=True)
            .execution_options(populate_existing=True)
        )
    else:
        approver = db.get(User, user_id)
    return bool(approver and approver.is_active and allowed(approver, "ticket_approve"))


def check_forward_recipient(db, ticket, user, address):
    if address == email(ticket.requester_email) and not ticket_approver(db, user.id, lock=True):
        fail("chair_approval_required", "Use the approved public reply flow to email this requester.", 403)


def current_approval(db, draft, *, lock=False):
    if not (draft.approved_by and draft.approved_at and ticket_approver(db, draft.approved_by, lock=lock)):
        return False
    revoked = db.scalar(select(Audit.id).where(
        Audit.entity_type == "users", Audit.entity_id == str(draft.approved_by),
        Audit.action == "user.ticket_approval_revoked", Audit.created_at >= draft.approved_at,
    ).limit(1))
    return revoked is None


def consume_approval(db, ticket, user, approval_id, kind, body, target_status, resolution_note, subject=None):
    if not approval_id:
        if allowed(user, "ticket_approve") and ticket_approver(db, user.id, lock=True):
            return
        fail("chair_approval_required", "Request chair approval for this reply before sending.", 403)
    draft = db.scalar(select(TicketDraft).where(TicketDraft.id == approval_id).with_for_update())
    if (
        not draft or draft.ticket_id != ticket.id or draft.created_by != user.id
        or draft.status != "approved" or not current_approval(db, draft, lock=True)
    ):
        fail("chair_approval_required", "This draft has not been approved for your account.", 403)
    if draft.base_version != ticket.version or not approval_matches(
        draft, ticket, kind, body, target_status, resolution_note, subject
    ):
        fail("approval_changed", "The approval does not match this action. Request chair approval for the reply.", 409)
    if kind == "notes" and draft.public_reply_approved:
        # Saving the private copy does not use the one-time public send approval.
        return
    draft.status = "used"
    db.execute(update(Notification).where(Notification.draft_id == draft.id).values(read_at=now()))


def queue_reply(db, ticket, user, body, request_id, subject):
    message = Outbound(recipient_email=contact_email(ticket.requester_email), recipient_name=ticket.requester_name, subject=subject, body_text=body, related_ticket_id=ticket.id, idempotency_key=f"ticket:{ticket.id}:{request_id}")
    db.add(message)
    db.flush()
    public = TicketMessage(ticket_id=ticket.id, message_type="staff_reply", sender_name=user.full_name, sender_email=user.email, body=body, created_by=user.id, outbound_message_id=message.id)
    db.add(public)
    ticket.last_message_at = now()
    audit(db, user, "ticket.replied", ticket, "Public reply queued in sandbox")
    return public


def apply_status_action(db, ticket, data, user):
    body = text_value(data.reply_body)
    internal = text_value(data.internal_note)
    resolution = text_value(data.resolution_note)
    payload = {"status": data.status, "reply_body": body, "internal_note": internal, "resolution_note": resolution}
    subject = reply_subject_value(ticket, data.reply_subject) if body else None
    if subject is not None and subject != default_reply_subject(ticket):
        # Preserve the signature for pre-migration actions and default subjects.
        payload["reply_subject"] = subject
    signature = hashlib.sha256(json.dumps(payload, sort_keys=True).encode()).hexdigest()
    existing = db.scalar(select(TicketAction).where(TicketAction.request_id == data.request_id))
    if existing:
        if existing.ticket_id != ticket.id or existing.created_by != user.id or existing.signature != signature:
            fail("request_reused", "This action ID was already used for a different change.", 409)
        return existing.result
    if data.expected_version is not None and data.expected_version != ticket.version:
        fail("ticket_changed", "This ticket changed. Review the latest messages before saving.", 409)
    if data.status != ticket.status and data.status not in TRANSITIONS[ticket.status]:
        fail("invalid_transition", f"Cannot move {ticket.status} to {data.status}.")
    if data.status == "waiting_on_requester" and not body:
        fail("reply_required", "A public reply is required for Waiting on requester.")
    if data.status == "waiting_on_organizer" and not internal:
        fail("internal_note_required", "Add an internal note explaining the team's next step.")
    if data.status == "resolved" and not resolution:
        fail("resolution_required", "A resolution note is required to resolve this ticket.")
    if body:
        if data.approval_id and internal:
            fail("approval_changed", "A private note was added to the approved reply. Save it separately.", 409)
        consume_approval(db, ticket, user, data.approval_id, "messages", body, data.status, resolution, subject)
    elif data.approval_id:
        consume_approval(db, ticket, user, data.approval_id, "notes" if internal else "messages", internal, data.status, resolution)
    before = serialize(ticket)
    public = queue_reply(db, ticket, user, body, data.request_id, subject) if body else None
    for kind, note_body in (("internal", internal), ("resolution", resolution)):
        if note_body:
            db.add(TicketNote(ticket_id=ticket.id, body=note_body, kind=kind, created_by=user.id))
            audit(db, user, "ticket.internal_note", ticket, "Resolution note added" if kind == "resolution" else "Private internal note added")
    ticket.status = data.status
    ticket.resolved_at = now() if data.status == "resolved" else ticket.resolved_at if data.status == "closed" else None
    ticket.closed_at = now() if data.status == "closed" else None
    ticket.auto_close_at = now() + timedelta(days=7) if data.status == "resolved" else None
    if data.status == "resolved":
        ticket.resolution_note = resolution
    ticket.updated_by = user.id
    changed(db, ticket)
    mark_ticket_notifications(db, ticket)
    audit(db, user, "ticket.status_changed", ticket, f"Ticket status: {data.status}", before, serialize(ticket))
    db.flush()
    result = {**serialize(ticket), "reply_message_id": str(public.id) if public else None}
    db.add(TicketAction(ticket_id=ticket.id, created_by=user.id, request_id=data.request_id, signature=signature, result=result))
    return result


def close_idle_tickets(db):
    items = db.scalars(select(Ticket).where(Ticket.status.in_(["waiting_on_requester", "resolved"]), Ticket.auto_close_at <= now()).order_by(Ticket.id).with_for_update(skip_locked=True).limit(100)).all()
    for ticket in items:
        before = serialize(ticket)
        ticket.status, ticket.closed_at, ticket.auto_close_at = "closed", now(), None
        changed(db, ticket)
        mark_ticket_notifications(db, ticket)
        audit(db, None, "ticket.auto_closed", ticket, "Ticket auto-closed after 7 days without a requester reply", before, serialize(ticket))
    db.commit()
    return len(items)
