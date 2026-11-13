"""Reset local sandbox activity. Pass --populate to load a fresh demo."""

import argparse
import json
import os
from datetime import timedelta
from random import Random
from uuid import uuid4

from sqlalchemy import delete, func, select, text

from .competition_catalog import CATEGORIES
from .db import Base, SessionLocal, engine, now
from .inbox import notify
from .models import (
    Campaign, InboundMessage, Judge, OfficialJudgeAssignment, Outbound,
    PilotCompetition, PilotDocument, PilotNomination, Recipient, Ticket, TicketMessage, User, ticket_sequence,
)
from .recruitment import notify_request

KEEP = {"users", "sessions", "committees", "competition_categories", "qualification_tags",
        "pilot_competitions", "pilot_documents"}
FIRST_NAMES = "Adrian Amara Avery Cameron Celeste Dalia Devon Eden Ellis Farah Hana Imani Iris Jules Kai Layla Leena Mateo Maya Mira Nadia Nico Noor Omar Quinn Rafa Remy Robin Rowan Samira Soren Zara".split()
LAST_NAMES = "Adler Amin Bennett Brooks Chen Das Ellis Flores Haddad Hayes Idris James Kim Lee Malik Martin Morgan Nguyen Park Patel Rahman Reed Rivera Santos Shah Silva Torres Vale Wang Young".split()
QUALIFICATIONS = (
    "Coaches a student team and has judged regional events.",
    "Teaches workshops and reviews student work using published rubrics.",
    "Has organized community tournaments and trained volunteer judges.",
    "Mentors students and has served on a competition review panel.",
)
NOTES = ("Available for the morning session.", "Prefers the afternoon session.",
         "Can cover either time slot.", "Asked for the scoring guide.")
QUESTIONS = (
    ("Competition check-in", "What time should our team arrive for check-in?"),
    ("Project submission deadline", "Can you confirm when our project is due?"),
    ("Materials for the event", "Which materials should we bring to the venue?"),
    ("Schedule conflict", "Two of our events overlap. Who can help with the schedule?"),
    ("Registration update", "How do I correct a name on our roster?"),
    ("Scoring rubric", "Where can our team find the current rubric?"),
)


def restore_catalog(db, director):
    """Keep the standard 2027 competition IDs and remove sandbox additions."""
    standard = {(slug, name) for slug, (_, names) in CATEGORIES.items() for name in names}
    standard.add(("arts", "Culinary Arts"))
    db.execute(delete(PilotDocument))
    catalog = {}
    for competition in db.scalars(select(PilotCompetition).order_by(
        PilotCompetition.deleted_at.is_not(None), PilotCompetition.created_at, PilotCompetition.id,
    )):
        key = (competition.category_slug, competition.name)
        if competition.event_year != 2027 or key not in standard or key in catalog:
            db.delete(competition)
        else:
            catalog[key] = competition
    db.flush()
    for slug, name in sorted(standard):
        competition = catalog.get((slug, name)) or PilotCompetition(
            name=name, event_year=2027, category_slug=slug, created_by=director.id,
        )
        competition.is_pilot = name == "Culinary Arts"
        competition.activation_status = "active"
        competition.deleted_at = competition.deleted_by = None
        db.add(competition)
        catalog[slug, name] = competition
    db.flush()
    return list(catalog.values())


def populate_demo(db, users):
    director, chair, organizer = (users[role] for role in ("director", "competitions_chair", "competitions_member"))
    competitions = restore_catalog(db, director)
    rng = Random(1729)
    names = iter(rng.sample([(first, last) for first in FIRST_NAMES for last in LAST_NAMES],
                            len(competitions) * 10 + 36))
    stamp = now()
    for index, competition in enumerate(competitions):
        created = stamp - timedelta(days=rng.randint(11, 24))
        sent = created + timedelta(days=2)
        campaign = Campaign(
            name=f"{competition.name} judge invitations", subject_template=f"Invitation to judge {competition.name}",
            body_template="Hello {{first_name}}, are you available to judge at the tournament?",
            pilot_competition_id=competition.id, event_year=competition.event_year,
            created_by=organizer.id, approved_by=chair.id, approved_at=sent,
            status="completed", created_at=created, updated_at=sent,
        )
        db.add(campaign)
        db.flush()
        states = ["pending", "pending", "approved", "approved", "waiting", "waiting",
                  "accepted", "declined", "rejected", "waiting"]
        for slot in range(index % 4):
            states[slot + 4] = "confirmed"
        for number, state in enumerate(states):
            first, last = next(names)
            address = f"{first}.{last}@judges.example.org".lower()
            name = f"{first} {last}"
            contacted = state in {"waiting", "accepted", "declined", "confirmed"}
            reviewed = state != "pending"
            judge = Judge(first_name=first, last_name=last, display_name=name, primary_email=address,
                          phone=f"(201) 555-01{(index * 10 + number) % 100:02d}", source="fabricated sandbox",
                          created_by=organizer.id, created_at=created, last_contacted_at=sent if contacted else None)
            db.add(judge)
            db.flush()
            status = {"waiting": "awaiting_response", "accepted": "accepted", "confirmed": "accepted",
                      "declined": "declined"}.get(state, "to_contact")
            nomination = PilotNomination(
                pilot_id=competition.id, judge_id=judge.id, requested_by=organizer.id, created_at=created,
                review_status="rejected" if state == "rejected" else "approved" if reviewed else "pending",
                reviewed_by=chair.id if reviewed else None, reviewed_at=created + timedelta(days=1) if reviewed else None,
                qualification_text=rng.choice(QUALIFICATIONS), request_notes=rng.choice(NOTES), status=status,
                last_contacted_at=sent if contacted else None,
                responded_at=sent + timedelta(days=1) if status in {"accepted", "declined"} else None,
                updated_at=sent if contacted else created,
            )
            db.add(nomination)
            if state == "confirmed":
                db.add(OfficialJudgeAssignment(
                    competition_id=competition.id, event_year=competition.event_year, slot_number=number - 3,
                    judge_id=judge.id, judge_name=name, confirmed_by=chair.id, created_at=sent + timedelta(days=2),
                ))
            if state == "pending":
                db.flush()
                notify_request(db, nomination, competition, judge, organizer)
            if contacted:
                recipient = Recipient(
                    campaign_id=campaign.id, judge_id=judge.id, recipient_name=name, recipient_email=address,
                    rendered_subject=campaign.subject_template, rendered_body=campaign.body_template.replace("{{first_name}}", first),
                    status="sent", sent_at=sent, created_at=sent,
                )
                db.add(recipient)
                db.flush()
                receipt = f"campaign:{recipient.id}"
                db.add(Outbound(
                    recipient_email=address, recipient_name=name, subject=recipient.rendered_subject,
                    body_text=recipient.rendered_body, related_campaign_recipient_id=recipient.id,
                    idempotency_key=receipt, provider="mock", provider_message_id=f"mock-{receipt}",
                    status="sent", attempt_count=1, completed_at=sent, created_at=sent,
                ))
                if nomination.responded_at:
                    reply = "I can judge this event." if status == "accepted" else "I am unavailable for this event."
                    unread = index % 5 == 0 and state == "accepted"
                    subject = f"Re: {recipient.rendered_subject}"
                    db.add(InboundMessage(
                        judge_id=judge.id, campaign_recipient_id=recipient.id, competition_id=competition.id,
                        sender_name=name, sender_email=address, subject=subject,
                        body=reply, request_id=uuid4(), created_at=nomination.responded_at,
                        read_at=None if unread else nomination.responded_at,
                    ))
                    if unread:
                        alert = notify(db, "judge_reply", f"{name} replied", subject,
                                       f"/campaigns?judge={judge.id}", judge_id=judge.id)
                        alert.created_at = nomination.responded_at
    for number in range(36):
        first, last = next(names)
        name = f"{first} {last}"
        address = f"{first}.{last}@teams.example.org".lower()
        subject, body = QUESTIONS[number % len(QUESTIONS)]
        status = ("new", "in_progress", "waiting_on_organizer", "resolved", "closed")[number % 5]
        created = stamp - timedelta(days=number % 7)
        sequence = db.scalar(select(ticket_sequence.next_value()))
        ticket = Ticket(
            public_number=f"MD-{sequence:06d}", requester_name=name, requester_email=address,
            subject=subject, status=status, source="simulated_email", created_by=organizer.id,
            created_at=created, last_message_at=created,
            resolved_at=created if status in {"resolved", "closed"} else None,
            closed_at=created if status == "closed" else None,
            resolution_note="Sent the requested information." if status in {"resolved", "closed"} else "",
        )
        db.add(ticket)
        db.flush()
        db.add(TicketMessage(ticket_id=ticket.id, message_type="requester_message",
                             sender_name=name, sender_email=address, body=body, created_at=created))
        db.add(InboundMessage(ticket_id=ticket.id, sender_name=name, sender_email=address,
                              subject=subject, body=body, request_id=uuid4(), created_at=created))
        if status == "new":
            notify(db, "ticket", f"Ticket email: {name}", subject, f"/tickets/{ticket.id}", ticket_id=ticket.id)
    db.flush()
    return {model.__tablename__: db.scalar(select(func.count()).select_from(model))
            for model in (Judge, PilotNomination, OfficialJudgeAssignment, Campaign, Outbound, InboundMessage, Ticket)}


def reset(populate=False):
    if os.getenv("MIST_SANDBOX_RESET") != "1" or engine.url.database != "mist" or engine.url.host != "db":
        raise RuntimeError("Reset requires MIST_SANDBOX_RESET=1 and the local Compose mist database")
    tables = [table.name for table in Base.metadata.sorted_tables if table.name not in KEEP]
    with SessionLocal.begin() as db:
        users = {user.role: user for user in db.scalars(select(User).where(User.is_active).order_by(User.created_at.desc()))}
        if populate and not {"director", "competitions_chair", "competitions_member"} <= users.keys():
            raise RuntimeError("Initialize the local sandbox accounts before loading demo data")
        db.execute(text("TRUNCATE " + ", ".join('"' + name + '"' for name in tables) + " RESTART IDENTITY"))
        db.execute(text("ALTER SEQUENCE ticket_number_seq RESTART WITH 1"))
        counts = populate_demo(db, users) if populate else {}
    print(json.dumps({"reset": "local sandbox activity", "demo_counts": counts}))


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--populate", action="store_true", help="Restore the standard catalog and add fabricated records")
    reset(parser.parse_args().populate)
