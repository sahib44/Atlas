"""Sequential HTTP import benchmark; fixtures stay in memory, never on disk.

Run the standalone scripts/benchmark-imports.yml Compose project, not the app stack.
One excluded warm-up and five measured runs per size; all reset to 1,000 contacts.
"""
import argparse
from collections import Counter, defaultdict
from datetime import datetime, timezone
import hashlib
import importlib.metadata
import io
import csv
import json
import os
from pathlib import Path
import platform
import random
import secrets
import statistics
import subprocess
import sys
import time
from uuid import NAMESPACE_URL, uuid5

import httpx
from sqlalchemy import func, select, text
from sqlalchemy.engine import make_url
from mist.auth import hasher
from mist.db import Base, SessionLocal, engine
from mist.models import (Audit, Campaign, Category, Duplicate, Engagement, ImportRow,
                         Judge, JudgeCategory, JudgeQualification, MergeEvent,
                         Outbound, Qualification, Recipient, User)
from mist.services import judge_views, serialize

SEED = 20261029
MAX_PHASE_SECONDS = 120
FIRST = """Aaliyah Aaron Abigail Adam Adrian Aisha Alejandro Alexandra Ali Alice
Amelia Amir Ana Andrew Angela Anika Anthony Arjun Asad Ayesha Benjamin Brandon
Brian Carlos Caroline Catherine Charlotte Chloe Christopher Daniel David Deepa
Diana Diego Eleanor Elena Elizabeth Emily Emma Eric Ethan Eva Fatima Felix
Gabriel Grace Hannah Hassan Henry Ibrahim Isaac Isabella Jack Jacob James
Jasmine Jason Javier Jessica Jonathan Joseph Joshua Julia Justin Karen Katherine
Khalid Layla Leah Liam Lily Lucas Luis Madison Maria Mariam Maya Mei Michael
Miguel Miriam Mohammed Nadia Nathan Nicholas Nicole Noah Nora Omar Olivia Oscar
Owen Patricia Priya Rachel Rafael Rebecca Rohan Ryan Sabrina Samuel Sana Sarah
Sebastian Sofia Sophia Steven Susan Tariq Thomas Timothy Valentina Victor Vivian
William Xavier Yasmin Youssef Yuna Zachary Zain Zoya""".split()
LAST = """Ahmed Ali Alvarez Anderson Baker Banerjee Bennett Brooks Brown Campbell
Carter Castillo Chen Clark Collins Cooper Cruz Davis Delgado Diaz Edwards Evans
Fernandez Flores Foster Garcia Gomez Gonzalez Green Gupta Hall Harris Hassan
Hernandez Hill Howard Hughes Ibrahim Jackson James Johnson Jones Kelly Khan Kim
King Kumar Lee Lewis Li Lopez Martin Martinez Mitchell Moore Morgan Morris
Murphy Myers Nakamura Nelson Nguyen Ortiz Parker Patel Perez Peterson Phillips
Powell Price Rahman Ramirez Ramos Reed Reyes Richardson Rivera Roberts Robinson
Rodriguez Rogers Romero Ross Ruiz Sanchez Sanders Scott Shah Sharma Singh Smith
Stewart Sullivan Taylor Thomas Thompson Torres Tran Turner Walker Wang Ward
Watson White Williams Wilson Wood Wright Xu Yang Young Zhang""".split()
ORGS = [f"{place} {kind}" for place in
        ("Cedar", "Maple", "Oak", "Pine", "River", "Lake", "Summit", "Valley", "Meadow", "Harbor")
        for kind in ("Arts Center", "Engineering Studio", "Research Institute", "Community College")]
STAMP = datetime(2026, 1, 1, tzinfo=timezone.utc)


def uid(label):
    return uuid5(NAMESPACE_URL, f"mist-import-benchmark:{SEED}:{label}")


def digest(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, default=str).encode()).hexdigest()


def fixtures(size):
    rng = random.Random(SEED)
    names = rng.sample([(a, b) for a in FIRST for b in LAST], 2000)
    directory = [dict(id=uid(f"judge:{i}"), display_name=f"{a} {b}", first_name=a,
                      last_name=b, primary_email=f"existing.{i}@example.org",
                      organization=rng.choice(ORGS), job_title="Community professional",
                      notes=f"Original history {i}", preserved_emails=[f"historical.{i}@example.org"],
                      status="prospective", source="fabricated benchmark baseline")
                 for i, (a, b) in enumerate(names[:1000])]
    targets = rng.sample(directory, 450)
    rows = []
    kinds = (["exact_email"] * (size * 30 // 100)
             + ["name_organization"] * (size * 10 // 100)
             + ["similar_organization"] * (size * 5 // 100)
             + ["invalid"] * (size * 5 // 100) + ["new"] * (size // 2))
    target_index = new_index = 0
    for i, kind in enumerate(kinds):
        target = None
        if kind in {"exact_email", "name_organization", "similar_organization"}:
            target = targets[target_index]
            target_index += 1
            name, org = target["display_name"], target["organization"]
            address = (f"  {target['primary_email'].upper()}  " if kind == "exact_email"
                       else f"updated.{i}@example.org")
            if kind == "similar_organization":
                name = name[:-1]  # A single omitted final surname character.
        else:
            a, b = names[1000 + new_index]
            new_index += 1
            name, org, address = f"{a} {b}", rng.choice(ORGS), f"incoming.{i}@example.org"
        data = dict(display_name=name, primary_email=address, organization=org,
                    job_title="Volunteer judge", notes=f"Imported evidence {i}",
                    qualifications="Mentoring", categories="Arts", years="2026")
        if kind == "invalid":
            if i % 2: data["primary_email"] = "invalid-address"
            else: data["years"] = "not-a-year"
        rows.append(dict(kind=kind, target=str(target["id"]) if target else None, data=data))
    random.Random(SEED + size).shuffle(rows)
    stream = io.StringIO(newline="")
    writer = csv.DictWriter(stream, fieldnames=list(rows[0]["data"]))
    writer.writeheader()
    writer.writerows(row["data"] for row in rows)
    return directory, rows, stream.getvalue().encode()


def reset(directory, password):
    with SessionLocal() as db:
        db.execute(text("TRUNCATE " + ",".join('"' + t.name + '"' for t in Base.metadata.sorted_tables)
                        + " RESTART IDENTITY CASCADE"))
        user = User(id=uid("director"), email="benchmark@example.org", full_name="Benchmark Director",
                    role="director", permissions=[], password_hash=hasher.hash(password))
        cat = Category(id=uid("category"), name="Arts")
        qual = Qualification(id=uid("qualification"), name="Professional experience")
        db.add_all([user, cat, qual]); db.flush()
        campaign = Campaign(id=uid("history-campaign"), name="Unsent historical preview",
                            subject_template="Prior subject", body_template="Prior body", created_by=user.id)
        db.add(campaign); db.flush()
        for i, data in enumerate(directory):
            db.add(Judge(**data, created_by=user.id, created_at=STAMP))
        db.flush()
        for i, data in enumerate(directory):
            jid = data["id"]
            db.add_all([JudgeCategory(judge_id=jid, category_id=cat.id),
                        JudgeQualification(judge_id=jid, qualification_id=qual.id),
                        Engagement(id=uid(f"engagement:{i}"), judge_id=jid, event_year=2025,
                                   outcome="served", notes=f"Original engagement {i}"),
                        Recipient(id=uid(f"recipient:{i}"), campaign_id=campaign.id, judge_id=jid,
                                  recipient_name=data["display_name"], recipient_email=data["primary_email"],
                                  rendered_subject="Prior subject", rendered_body=f"Original preview {i}"),
                        Audit(id=uid(f"audit:{i}"), actor_user_id=user.id, action="benchmark.original",
                              entity_type="judge_contacts", entity_id=str(jid), summary=f"Original audit {i}")])
        db.commit()
        return snapshot(db)


def snapshot(db):
    # Import legitimately adds 2026 engagements; protect the original 2025 records.
    return {cls.__tablename__: digest([serialize(row) for row in db.scalars(
                select(cls).where(cls.id.in_([uid(f"{kind}:{i}") for i in range(1000)])).order_by(cls.id))])
            for cls, kind in ((Engagement, "engagement"), (Recipient, "recipient"), (Audit, "audit"))}


def request(client, method, path, **kwargs):
    started = time.perf_counter()
    response = client.request(method, "/api" + path, **kwargs)
    elapsed = time.perf_counter() - started
    if response.status_code != 200:
        raise RuntimeError(f"{method} {path}: HTTP {response.status_code}")
    if elapsed > MAX_PHASE_SECONDS:
        raise RuntimeError(f"{path} exceeded the {MAX_PHASE_SECONDS}s stop threshold")
    return response.json(), elapsed


def run(client, size, iteration, warmup, password):
    directory, truth, csv_bytes = fixtures(size)
    original_history = reset(directory, password)
    request(client, "POST", "/auth/login", json={"email": "benchmark@example.org", "password": password})
    result = dict(size=size, iteration=iteration, warmup=warmup,
                  fixture_sha256=hashlib.sha256(csv_bytes).hexdigest(), directory_sha256=digest(directory),
                  csv_bytes=len(csv_bytes), composition=dict(Counter(row["kind"] for row in truth)))
    batch, result["upload_s"] = request(client, "POST", "/imports", files={"file": ("fabricated.csv", csv_bytes, "text/csv")})
    bid = batch["id"]
    _, result["mapping_s"] = request(client, "PUT", f"/imports/{bid}/mapping", json={"mapping": batch["mapping"]})
    _, result["analysis_s"] = request(client, "POST", f"/imports/{bid}/analyze")
    with SessionLocal() as db:
        rows = list(db.scalars(select(ImportRow).where(ImportRow.batch_id == bid).order_by(ImportRow.row_number)))
        candidates = list(db.scalars(select(Duplicate).join(ImportRow, Duplicate.import_row_id == ImportRow.id).where(ImportRow.batch_id == bid)))
        by_row = defaultdict(list)
        for candidate in candidates: by_row[candidate.import_row_id].append(candidate)
        result["candidate_types"] = dict(Counter(c.match_type for c in candidates))
        result["review_rows"] = sum(row.resolution == "duplicate_review" for row in rows)
        incidental_strong = incidental_weak = 0
        decisions = []
        for row, intended in zip(rows, truth, strict=True):
            options = by_row[row.id]
            for c in options:
                intended_match = intended["target"] and str(c.existing_judge_id) == intended["target"]
                if not intended_match:
                    if c.confidence_score >= .7: incidental_strong += 1
                    else: incidental_weak += 1
            if intended["kind"] == "invalid":
                assert row.resolution == "invalid" and row.validation_errors
            elif intended["target"]:
                chosen = next((c for c in options if str(c.existing_judge_id) == intended["target"]
                               and c.match_type == intended["kind"]), None)
                assert chosen and row.resolution == "duplicate_review", "Expected duplicate missing"
                decisions.append((str(chosen.id), "merge"))
            else:
                assert row.resolution in {"create", "duplicate_review"}
                if row.resolution == "duplicate_review":
                    decisions.append((str(next(c for c in options if c.confidence_score >= .7).id), "create"))
        result.update(incidental_strong_candidates=incidental_strong, incidental_weak_candidates=incidental_weak,
                      expected_duplicate_targets_found=sum(bool(row["target"]) for row in truth))
    # Fixture oracle supplies decisions through the real review endpoint; no timing claims about humans.
    review_started = time.perf_counter()
    for cid, decision in decisions:
        request(client, "POST", f"/duplicates/{cid}/resolve", json={"decision": decision, "confirmed": True,
                "field_decisions": {"job_title": "incoming"} if decision == "merge" else {}})
    result["scripted_review_s"] = time.perf_counter() - review_started
    committed, result["commit_s"] = request(client, "POST", f"/imports/{bid}/commit", json={"confirmed": True})
    expected = (size // 2, size * 45 // 100, size * 5 // 100)
    observed = tuple(committed[k] for k in ("created_count", "updated_count", "skipped_count"))
    assert observed == expected, (observed, expected)
    with SessionLocal() as db:
        assert db.scalar(select(func.count()).select_from(Judge)) == 1000 + expected[0]
        assert db.scalar(select(func.count()).select_from(Outbound)) == 0
        assert db.scalar(select(func.count()).select_from(MergeEvent)) == expected[1]
        current = snapshot(db)
        assert current == original_history, "Existing engagement/preview/audit changed"
        assert db.scalar(select(func.count()).select_from(Engagement)) == 1000 + size * 95 // 100
        for original, view in zip(directory, judge_views(db, [db.get(Judge, item["id"]) for item in directory]), strict=True):
            assert original["notes"] in view["notes"] and original["primary_email"] == view["primary_email"]
            assert set(original["preserved_emails"]) <= set(view["preserved_emails"])
            assert "Professional experience" in view["qualifications"] and "Arts" in view["categories"]
            assert 2025 in view["years"]
        for event in db.scalars(select(MergeEvent)):
            before = event.before_snapshot["existing"]
            assert before["job_title"] == "Community professional" and "Original history" in before["notes"]
        state = {cls.__tablename__: digest([serialize(row) for row in db.scalars(select(cls).order_by(cls.id))])
                 for cls in (Judge, MergeEvent, ImportRow, Audit, Engagement, Recipient, Outbound)}
    again, result["repeat_commit_s"] = request(client, "POST", f"/imports/{bid}/commit", json={"confirmed": True})
    assert tuple(again[k] for k in ("created_count", "updated_count", "skipped_count")) == expected
    with SessionLocal() as db:
        for cls in (Judge, MergeEvent, ImportRow, Audit, Engagement, Recipient, Outbound):
            assert digest([serialize(row) for row in db.scalars(select(cls).order_by(cls.id))]) == state[cls.__tablename__]
    result.update(created=expected[0], updated=expected[1], skipped=expected[2], final_contacts=1000 + expected[0],
                  checks="passed", machine_phases_s=sum(result[k] for k in ("upload_s", "analysis_s", "commit_s")))
    print(json.dumps({k: v for k, v in result.items() if k not in {"fixture_sha256", "directory_sha256"}}), flush=True)
    return result


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    url = make_url(os.environ["DATABASE_URL"])
    if not (os.getenv("MIST_IMPORT_BENCHMARK") == "1" and url.host == "db"
            and url.database == "mist_import_benchmark" and url.username == "mist_benchmark"):
        raise SystemExit("Refusing to reset any database except the dedicated benchmark database")
    subprocess.run(["alembic", "upgrade", "head"], check=True)
    with engine.connect() as conn:
        postgres_version = conn.scalar(text("SELECT version()"))
    report = dict(seed=SEED, source_revision=os.environ["MIST_SOURCE_REVISION"],
                  started_at=datetime.now(timezone.utc).isoformat(), starting_contacts=1000,
                  environment={"platform": platform.platform(), "python": platform.python_version(),
                               "postgres": postgres_version, "cpus_visible": os.cpu_count(),
                               "packages": {p: importlib.metadata.version(p) for p in ("fastapi", "uvicorn", "SQLAlchemy", "psycopg", "httpx")}},
                  warmups_per_size=1, measured_repetitions=5, stop_after_phase_s=MAX_PHASE_SECONDS,
                  runs=[], failures=[])
    password = secrets.token_urlsafe(24)
    # Real loopback HTTP, normal middleware/auth/serialization/SQL, one unmodified Uvicorn worker.
    server = subprocess.Popen([sys.executable, "-m", "uvicorn", "mist.main:app", "--host", "127.0.0.1", "--port", "18088", "--no-access-log"], stdout=subprocess.DEVNULL)
    try:
        with httpx.Client(base_url="http://127.0.0.1:18088", headers={"X-MIST-Request": "1"}, timeout=MAX_PHASE_SECONDS, trust_env=False) as client:
            for _ in range(100):
                try:
                    if client.get("/health").status_code == 200: break
                except httpx.ConnectError: pass
                time.sleep(.1)
            else: raise RuntimeError("API failed to start")
            for size in (100, 500, 1000):
                for iteration in range(6):
                    report["runs"].append(run(client, size, iteration, iteration == 0, password))
                    args.output.write_text(json.dumps(report, indent=2) + "\n")
        report["summary"] = {}
        for size in (100, 500, 1000):
            runs = [r for r in report["runs"] if r["size"] == size and not r["warmup"]]
            report["summary"][size] = {phase: dict(median=statistics.median(r[phase] for r in runs),
                minimum=min(r[phase] for r in runs), maximum=max(r[phase] for r in runs))
                for phase in ("upload_s", "mapping_s", "analysis_s", "scripted_review_s", "commit_s", "repeat_commit_s", "machine_phases_s")}
    except Exception as exc:
        report["failures"].append({"type": type(exc).__name__, "message": str(exc),
                                   "completed_runs": len(report["runs"])})
        raise
    finally:
        # Stop slow work too; never tune the implementation to make a workload pass.
        server.terminate()
        try: server.wait(timeout=5)
        except subprocess.TimeoutExpired: server.kill(); server.wait()
        report["finished_at"] = datetime.now(timezone.utc).isoformat()
        args.output.write_text(json.dumps(report, indent=2) + "\n")


if __name__ == "__main__":
    main()
