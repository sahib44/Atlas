from datetime import timedelta
from uuid import UUID, uuid4, uuid5
from fastapi import APIRouter, Depends, Query, Request
from sqlalchemy import select, or_, update
from .db import get_db, now
from .models import *
from .schemas import (
    TicketIn,
    TicketStatus,
    TicketDraftApprovalIn,
    TicketDraftIn,
    Content,
    ObjectView,
    Page,
    Input,
)
from .auth import current_user, allowed, fail
from .services import *

router = APIRouter(prefix="/api")
from .ticket_flow import (
    TRANSITIONS,
    STATUSES,
    ForwardRequest,
    apply_status_action,
    approval_matches,
    can_approve_public_reuse,
    changed,
    check_forward_recipient,
    consume_approval,
    current_approval,
    default_reply_subject,
    draft_reply_subject,
    forward_request,
    reply_subject_value,
    supersede_drafts,
    text_value,
    ticket_approver,
)


class TicketPage(Page):
    counts: dict[str, int]


def ticket_scope(user):
    # Every ticket belongs to the shared competitions mailbox.
    return select(Ticket).where(allowed(user, "tickets") or "private_tickets" in user.permissions)


def ticket_user(user=Depends(current_user)):
    if not (allowed(user, "tickets") or "private_tickets" in user.permissions):
        fail("forbidden", "Your account has no private ticket access.", 403)
    return user


def ticket_get(db, user, id, lock=False):
    stmt = ticket_scope(user).where(Ticket.id == id)
    if lock:
        stmt = stmt.with_for_update()
    ticket = db.scalar(stmt)
    if not ticket:
        fail("not_found", "Ticket not found or access is restricted.", 404)
    return ticket


@router.get("/tickets", response_model=TicketPage)
def tickets(
    request: Request,
    page_no: int = Query(1, alias="page", ge=1),
    page_size: int = Query(25, ge=1, le=100),
    user=Depends(ticket_user),
    db=Depends(get_db),
):
    params = request.query_params
    stmt = ticket_scope(user)
    if q := params.get("q"):
        stmt = stmt.where(
            or_(
                Ticket.subject.ilike(f"%{q}%"),
                Ticket.public_number.ilike(f"%{q}%"),
                Ticket.requester_name.ilike(f"%{q}%"),
                Ticket.requester_email.ilike(f"%{q}%"),
            )
        )
    if value := params.get("status"):
        if value not in TRANSITIONS:
            fail("invalid_status", "Choose a valid ticket status.")
        stmt = stmt.where(Ticket.status == value)
    open_ticket = Ticket.status.not_in(["resolved", "closed"])
    views = {
        "open": open_ticket,
        "waiting": Ticket.status.in_(["new", "in_progress", "waiting_on_organizer"]),
        "overdue": open_ticket & (Ticket.last_message_at < now() - timedelta(days=3)),
    }
    counts = db.execute(stmt.with_only_columns(
        func.count(Ticket.id).label("all"),
        *(func.count(Ticket.id).filter(condition).label(name) for name, condition in views.items()),
    )).mappings().one()
    condition = views.get(params.get("view", ""))
    if condition is not None:
        stmt = stmt.where(condition)
    sort = params.get("sort", "newest")
    ordering = {
        "newest": Ticket.created_at.desc(),
        "oldest": Ticket.created_at,
        "activity": Ticket.last_message_at.desc(),
    }
    result = page(
        db,
        stmt.order_by(ordering.get(sort, Ticket.created_at.desc()), Ticket.id),
        page_no,
        page_size,
    )
    ids = [UUID(item["id"]) for item in result["items"]]
    if ids:
        latest = dict(db.execute(
            select(TicketMessage.ticket_id, func.left(TicketMessage.body, 160))
            .where(TicketMessage.ticket_id.in_(ids)).distinct(TicketMessage.ticket_id)
            .order_by(TicketMessage.ticket_id, TicketMessage.created_at.desc(), TicketMessage.id.desc())
        ).all())
        unread = set(db.scalars(select(Notification.ticket_id).where(
            Notification.ticket_id.in_(ids), Notification.kind == "ticket",
            Notification.recipient_user_id.is_(None), Notification.read_at.is_(None),
        )))
        for item, id in zip(result["items"], ids):
            item["preview"], item["unread"] = latest.get(id, ""), id in unread
    return {**result, "counts": dict(counts)}


@router.post("/tickets", response_model=ObjectView)
def create(data: TicketIn, user=Depends(ticket_user), db=Depends(get_db)):
    address = contact_email(data.requester_email)
    name, subject, body = clean(data.requester_name), clean(data.subject), clean(data.body)
    ticket_id = uuid5(user.id, f"ticket-create:{data.request_id}") if data.request_id else uuid4()
    if data.request_id:
        # Serialize retries before testing the deterministic ticket ID.
        db.execute(select(func.pg_advisory_xact_lock(int.from_bytes(ticket_id.bytes[:8], "big", signed=True))))
        existing = db.get(Ticket, ticket_id)
        if existing:
            initial_body = db.scalar(
                select(TicketMessage.body)
                .where(TicketMessage.ticket_id == ticket_id, TicketMessage.message_type == "requester_message")
                .order_by(TicketMessage.created_at, TicketMessage.id)
                .limit(1)
            )
            if (existing.requester_email, existing.requester_name, existing.subject, initial_body) != (address, name, subject, body):
                fail("request_reused", "This request ID was already used for a different ticket.", 409)
            return serialize(existing)
    number = db.scalar(select(ticket_sequence.next_value()))
    ticket = Ticket(
        id=ticket_id,
        public_number=f"MD-{number:06d}",
        requester_name=name,
        requester_email=address,
        subject=subject,
        source="simulated_email",
        created_by=user.id,
    )
    db.add(ticket)
    db.flush()
    db.add(
        TicketMessage(
            ticket_id=ticket.id,
            message_type="requester_message",
            sender_name=ticket.requester_name,
            sender_email=address,
            body=body,
            created_by=user.id,
        )
    )
    audit(db, user, "ticket.created", ticket, "Incoming competitions ticket created")
    from .inbox import notify
    notify(db, "ticket", f"New ticket: {ticket.requester_name}", ticket.subject, f"/tickets/{ticket.id}", ticket_id=ticket.id)
    db.commit()
    return serialize(ticket)


class TicketBulk(Input):
    ids: list[UUID]
    status: TicketStatus
    confirmed: bool


@router.post("/tickets/bulk")
def bulk(data: TicketBulk, user=Depends(ticket_user), db=Depends(get_db)):
    if not data.confirmed or not 1 <= len(data.ids) <= 1000:
        fail("confirmation_required", "Confirm between 1 and 1000 tickets.")
    if data.status.status not in {"in_progress", "closed"}:
        fail("individual_status_required", "Open each ticket to provide its required reply or note.")
    for id in sorted(set(data.ids)):
        ticket = ticket_get(db, user, id, True)
        action = data.status.model_copy(update={"request_id": uuid5(data.status.request_id, str(id))})
        apply_status_action(db, ticket, action, user)
    db.commit()
    return {"updated": len(set(data.ids))}


@router.get("/tickets/{id}", response_model=ObjectView)
def detail(id: UUID, user=Depends(ticket_user), db=Depends(get_db)):
    ticket = ticket_get(db, user, id)
    return {
        **serialize(ticket),
        "allowed_transitions": sorted(TRANSITIONS[ticket.status]),
        "reply_subject": default_reply_subject(ticket),
    }


def draft_response(draft, ticket):
    return {**serialize(draft), "reply_subject": draft_reply_subject(draft, ticket)}


@router.get("/tickets/{id}/drafts", response_model=Page)
def ticket_drafts(id: UUID, user=Depends(ticket_user), db=Depends(get_db)):
    ticket = ticket_get(db, user, id)
    stmt = select(TicketDraft).where(TicketDraft.ticket_id == id)
    if not allowed(user, "ticket_approve"):
        stmt = stmt.where(TicketDraft.created_by == user.id)

    def view(draft):
        author = db.get(User, draft.created_by)
        data = draft_response(draft, ticket)
        if draft.status == "approved" and not current_approval(db, draft):
            data["status"] = "superseded"
        return {**data, "author_name": author.full_name if author else "Organizer"}

    return page(db, stmt.order_by(TicketDraft.created_at.desc(), TicketDraft.id.desc()), 1, 100, view)


@router.post("/tickets/{id}/drafts", response_model=ObjectView)
def request_chair_review(id: UUID, data: TicketDraftIn, user=Depends(ticket_user), db=Depends(get_db)):
    ticket = ticket_get(db, user, id, True)
    body, resolution = text_value(data.body), text_value(data.resolution_note)
    subject = reply_subject_value(ticket, data.reply_subject)
    if data.target_status and data.target_status not in STATUSES:
        fail("invalid_status", "Choose a valid ticket status.")
    if not body and not (data.target_status == "resolved" and resolution):
        fail("draft_required", "Write a draft before requesting chair approval.")
    existing = db.scalar(select(TicketDraft).where(TicketDraft.request_id == data.request_id))
    if existing:
        if (
            existing.ticket_id != id or existing.created_by != user.id or existing.kind != data.kind
            or not approval_matches(existing, ticket, data.kind, body, data.target_status, resolution, subject)
        ):
            fail("request_reused", "This request ID was already used.", 409)
        return draft_response(existing, ticket)
    if data.expected_version != ticket.version:
        fail("ticket_changed", "The ticket changed. Review the latest messages before requesting approval.", 409)
    candidates = db.scalars(select(TicketDraft).where(
        TicketDraft.ticket_id == id, TicketDraft.created_by == user.id, TicketDraft.base_version == ticket.version,
        TicketDraft.body == body, TicketDraft.status.in_(["pending_approval", "approved"]),
    ).order_by(TicketDraft.created_at.desc(), TicketDraft.id.desc()))
    pending = None
    for candidate in candidates:
        if not approval_matches(candidate, ticket, data.kind, body, data.target_status, resolution, subject):
            continue
        if candidate.status == "approved" and current_approval(db, candidate):
            return draft_response(candidate, ticket)
        if candidate.status == "pending_approval" and pending is None:
            pending = candidate
    if pending:
        return draft_response(pending, ticket)
    chairs = db.scalars(select(User).where(
        User.role == "competitions_chair", User.is_active.is_(True), User.managed_account == user.managed_account,
    )).all()
    if not chairs:
        fail("chair_unavailable", "No active competitions chair is available for approval.")
    supersede_drafts(db, ticket, author_id=user.id, kind=data.kind)
    draft = TicketDraft(ticket_id=id, created_by=user.id, kind=data.kind, body=body, reply_subject=subject, target_status=data.target_status, resolution_note=resolution, base_version=ticket.version, request_id=data.request_id)
    db.add(draft)
    db.flush()
    from .inbox import notify
    for chair in chairs:
        notify(db, "ticket_approval_requested", f"Chair approval requested: {ticket.public_number}", f"{user.full_name} submitted a {'reply' if data.kind == 'messages' else 'private note'} for review.", f"/tickets/{id}?draft={draft.id}", ticket_id=id, draft_id=draft.id, recipient_user_id=chair.id)
    audit(db, user, "ticket.approval_requested", ticket, "Draft submitted for chair approval", after={"draft_id": str(draft.id)})
    db.commit()
    return draft_response(draft, ticket)


@router.post("/tickets/{id}/drafts/{draft_id}/approve", response_model=ObjectView)
def approve_ticket_draft(id: UUID, draft_id: UUID, data: TicketDraftApprovalIn | None = None, user=Depends(ticket_user), db=Depends(get_db)):
    if not allowed(user, "ticket_approve"):
        fail("forbidden", "Only the competitions chair or a director can approve ticket drafts.", 403)
    ticket = ticket_get(db, user, id, True)
    draft = get(db, TicketDraft, draft_id, True)
    if draft.ticket_id != id:
        fail("not_found", "Draft not found in this ticket.", 404)
    if not ticket_approver(db, user.id, lock=True):
        fail("forbidden", "Only the competitions chair or a director can approve ticket drafts.", 403)
    if draft.base_version != ticket.version or draft.status not in {"pending_approval", "approved"}:
        fail("stale_draft", "This draft or ticket changed. Ask the head to submit a fresh draft.", 409)
    allow_public_reply = bool(data and data.allow_public_reply)
    if allow_public_reply and not can_approve_public_reuse(draft):
        fail("private_approval_only", "Only a standalone internal draft can be approved as a public reply.", 409)
    if draft.status == "approved":
        if not current_approval(db, draft, lock=True):
            fail("stale_draft", "This approval is no longer valid. Ask the head to submit a fresh draft.", 409)
        if allow_public_reply and not draft.public_reply_approved:
            fail("approval_scope_unchanged", "This approval was private. Submit a public draft for approval.", 409)
        return draft_response(draft, ticket)
    draft.status, draft.approved_by, draft.approved_at = "approved", user.id, now()
    draft.reply_subject = draft_reply_subject(draft, ticket)
    draft.public_reply_approved = allow_public_reply
    db.execute(update(Notification).where(Notification.draft_id == draft.id).values(read_at=now()))
    from .inbox import notify
    notice = ("The chair approved your draft for a public reply. Open the ticket to review it before sending."
              if allow_public_reply else "The chair approved your draft. Open the ticket to review it and make the final send or save.")
    notify(db, "ticket_draft_approved", f"Draft approved: {ticket.public_number}", notice, f"/tickets/{id}?draft={draft.id}", ticket_id=id, draft_id=draft.id, recipient_user_id=draft.created_by)
    audit(db, user, "ticket.draft_approved", ticket, "Chair approved a ticket draft",
          after={"draft_id": str(draft.id), "public_reply_approved": allow_public_reply})
    db.commit()
    return draft_response(draft, ticket)


@router.get("/tickets/{id}/{kind}", response_model=Page)
def thread(
    id: UUID,
    kind: str,
    page_no: int = Query(1, alias="page", ge=1),
    page_size: int = Query(25, ge=1, le=100),
    message_page: int | None = Query(None, ge=1),
    user=Depends(ticket_user),
    db=Depends(get_db),
):
    ticket_get(db, user, id)
    cls = {"messages": TicketMessage, "notes": TicketNote, "activity": Audit}.get(kind)
    if not cls:
        fail("not_found", "Unknown ticket view.", 404)
    stmt = (
        select(cls).where(Audit.entity_id == str(id))
        if cls == Audit
        else select(cls).where(cls.ticket_id == id)
    )
    if kind == "notes" and message_page is not None:
        # Keep timeline notes in the same time window as the visible email page.
        def boundary(number):
            return db.scalar(select(TicketMessage.created_at).where(
                TicketMessage.ticket_id == id,
            ).order_by(TicketMessage.created_at, TicketMessage.id).offset((number - 1) * 25).limit(1))

        if message_page > 1 and (start := boundary(message_page)):
            stmt = stmt.where(TicketNote.created_at >= start)
        if end := boundary(message_page + 1):
            stmt = stmt.where(TicketNote.created_at < end)
    order = (cls.created_at.desc(), cls.id.desc()) if kind == "notes" else (cls.created_at, cls.id)
    result = page(db, stmt.order_by(*order), page_no, page_size)
    if kind == "messages":
        ids = [UUID(item["outbound_message_id"]) for item in result["items"] if item["outbound_message_id"]]
        if ids:
            delivery = {outbound_id: (status, subject) for outbound_id, status, subject in db.execute(
                select(Outbound.id, Outbound.status, Outbound.subject).where(
                Outbound.id.in_(ids), Outbound.related_ticket_id == id,
            ))}
            for item in result["items"]:
                if outbound_id := item["outbound_message_id"]:
                    item["delivery_status"], item["subject"] = delivery.get(UUID(outbound_id), (None, None))
    if kind == "notes":
        ids = {UUID(item["created_by"]) for item in result["items"] if item["created_by"]}
        if ids:
            names = dict(db.execute(select(User.id, User.full_name).where(User.id.in_(ids))).all())
            for item in result["items"]:
                if author := item["created_by"]:
                    item["author_name"] = names.get(UUID(author))
    return result


@router.post("/tickets/{id}/status", response_model=ObjectView)
def status(id: UUID, data: TicketStatus, user=Depends(ticket_user), db=Depends(get_db)):
    ticket = ticket_get(db, user, id, True)
    result = apply_status_action(db, ticket, data, user)
    db.commit()
    return result


@router.post("/tickets/{id}/messages", response_model=ObjectView)
def reply(id: UUID, data: Content, user=Depends(ticket_user), db=Depends(get_db)):
    ticket = ticket_get(db, user, id, True)
    result = apply_status_action(db, ticket, TicketStatus(status="waiting_on_requester", reply_body=data.body, reply_subject=data.reply_subject, request_id=data.request_id, approval_id=data.approval_id), user)
    public = get(db, TicketMessage, UUID(result["reply_message_id"]))
    subject = reply_message_subject(db, ticket, public)
    db.commit()
    return {**serialize(public), "subject": subject}


@router.post("/tickets/{id}/notes", response_model=ObjectView)
def note(id: UUID, data: Content, user=Depends(ticket_user), db=Depends(get_db)):
    ticket = ticket_get(db, user, id, True)
    body = text_value(data.body)
    if not body:
        fail("note_required", "Enter an internal note.")
    existing = db.scalar(select(TicketNote).where(TicketNote.request_id == data.request_id))
    if existing:
        if existing.ticket_id != id or existing.created_by != user.id or existing.body != body:
            fail("request_reused", "This note ID was already used.", 409)
        return serialize(existing)
    if data.approval_id:
        consume_approval(db, ticket, user, data.approval_id, "notes", body, "", "")
    note = TicketNote(ticket_id=id, body=body, created_by=user.id, request_id=data.request_id)
    db.add(note)
    changed(db, ticket, private_note_only=True)
    audit(db, user, "ticket.internal_note", ticket, "Private internal note added")
    db.commit()
    return serialize(note)


def reply_message_subject(db, ticket, message):
    if message.message_type == "staff_reply" and message.outbound_message_id:
        stored = db.scalar(select(Outbound.subject).where(
            Outbound.id == message.outbound_message_id, Outbound.related_ticket_id == ticket.id,
        ))
        if stored:
            return stored
    return ticket.subject


@router.post("/tickets/{id}/messages/{message_id}/forward", response_model=ObjectView)
def forward_ticket_message(id: UUID, message_id: UUID, data: ForwardRequest, user=Depends(ticket_user), db=Depends(get_db)):
    ticket = ticket_get(db, user, id)
    original = get(db, TicketMessage, message_id)
    if original.ticket_id != id:
        fail("not_found", "Message not found in this ticket.", 404)
    address = contact_email(data.recipient_email)
    check_forward_recipient(db, ticket, user, address)
    note = text_value(data.note)
    key, existing = forward_request(db, "ticket-forward", user.id, data.request_id, original.id, address, note)
    if existing:
        return serialize(existing)
    subject = reply_message_subject(db, ticket, original)
    body = (note + "\n\n") if note else ""
    body += f"---------- Forwarded message ----------\nFrom: {original.sender_name} <{original.sender_email}>\nSubject: {subject}\n\n{original.body}"
    message = Outbound(recipient_email=address, recipient_name=address, subject=f"Fwd: {subject}", body_text=body, related_ticket_id=id, idempotency_key=key)
    db.add(message)
    audit(db, user, "ticket.message_forwarded", ticket, "Queued a sandbox email forward", after={"recipient_email": address, "message_id": str(message_id)})
    db.commit()
    return serialize(message)
