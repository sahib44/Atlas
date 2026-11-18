import csv
import io
import os
from pathlib import PurePath
from uuid import UUID
from fastapi import APIRouter, Depends, File, HTTPException, UploadFile, Query, Response
from sqlalchemy import select, delete
from .db import get_db, now
from .models import *
from .schemas import Page, ObjectView, Mapping, Confirm, Resolve
from .auth import require, fail
from .services import *

router = APIRouter(prefix="/api")


@router.post("/imports", response_model=ObjectView)
async def upload(
    file: UploadFile = File(...), user=Depends(require("imports")), db=Depends(get_db)
):
    if not (file.filename or "").lower().endswith(".csv"):
        fail("invalid_file", "Upload a .csv file.")
    limit = int(os.getenv("IMPORT_MAX_BYTES", "2097152"))
    raw = await file.read(limit + 1)
    if len(raw) > limit:
        fail("file_too_large", f"CSV exceeds {limit} bytes.")
    try:
        content = raw.decode("utf-8-sig")
    except UnicodeDecodeError:
        fail("invalid_encoding", "Save the CSV as UTF-8 before uploading.")
    if not content.strip() or "\x00" in content:
        fail("invalid_csv", "CSV is empty or contains null bytes.")
    try:
        try:
            dialect = csv.Sniffer().sniff(content[:8192], delimiters=",;\t")
        except csv.Error:
            dialect = csv.excel
        reader = csv.DictReader(io.StringIO(content), dialect=dialect, strict=True)
        headers = reader.fieldnames
        if (
            not headers
            or any(not h.strip() for h in headers)
            or len(set(headers)) != len(headers)
            or len(headers) > 100
        ):
            fail(
                "invalid_headers",
                "CSV needs unique, nonempty column headers (maximum 100).",
            )
        rows = []
        for row in reader:
            if None in row or any(v is None for v in row.values()):
                fail(
                    "malformed_csv",
                    "Each row must have the same number of columns as the header.",
                )
            rows.append(row)
            if len(rows) > int(os.getenv("IMPORT_MAX_ROWS", "5000")):
                fail("too_many_rows", "CSV exceeds the configured row limit.")
    except csv.Error:
        fail("malformed_csv", "CSV quoting or delimiters are malformed.")
    if not rows:
        fail("empty_csv", "CSV contains no data rows.")
    aliases = {
        "email": "primary_email",
        "name": "display_name",
        "full_name": "display_name",
        "years_judged": "years",
        "first_name": "first_name",
        "last_name": "last_name",
        "title": "job_title",
    }
    mapping = {}
    for header in headers:
        key = normalized(header).replace(" ", "_")
        key = aliases.get(key, key)
        if key in FIELDS and key not in mapping:
            mapping[key] = header
    batch = ImportBatch(
        filename=PurePath(file.filename).name[:255],
        uploaded_by=user.id,
        total_rows=len(rows),
        metadata_json={
            "size": len(raw),
            "encoding": "UTF-8",
            "delimiter": dialect.delimiter,
            "headers": headers,
        },
        mapping=mapping,
    )
    db.add(batch)
    db.flush()
    db.add_all(
        [
            ImportRow(batch_id=batch.id, row_number=i + 2, raw_data=row)
            for i, row in enumerate(rows)
        ]
    )
    audit(db, user, "import.uploaded", batch, f"Uploaded {len(rows)} rows")
    db.commit()
    return serialize(batch)


@router.get("/imports", response_model=Page)
def imports(
    page_no: int = Query(1, alias="page", ge=1),
    page_size: int = Query(25, ge=1, le=100),
    user=Depends(require("imports")),
    db=Depends(get_db),
):
    return page(
        db,
        select(ImportBatch).order_by(ImportBatch.created_at.desc()),
        page_no,
        page_size,
    )


@router.get("/imports/{id}", response_model=ObjectView)
def batch(id: UUID, user=Depends(require("imports")), db=Depends(get_db)):
    result = serialize(get(db, ImportBatch, id))
    result["counts"] = dict(
        db.execute(
            select(ImportRow.resolution, func.count())
            .where(ImportRow.batch_id == id)
            .group_by(ImportRow.resolution)
        ).all()
    )
    return result


@router.get("/imports/{id}/rows", response_model=Page)
def rows(
    id: UUID,
    resolution: str = "",
    page_no: int = Query(1, alias="page", ge=1),
    page_size: int = Query(25, ge=1, le=100),
    user=Depends(require("imports")),
    db=Depends(get_db),
):
    stmt = (
        select(ImportRow).where(ImportRow.batch_id == id).order_by(ImportRow.row_number)
    )
    if resolution:
        stmt = stmt.where(ImportRow.resolution == resolution)
    return page(db, stmt, page_no, page_size)


@router.put("/imports/{id}/mapping", response_model=ObjectView)
def mapping(
    id: UUID, data: Mapping, user=Depends(require("imports")), db=Depends(get_db)
):
    batch = get(db, ImportBatch, id, True)
    if batch.status not in {"uploaded", "mapped"}:
        fail(
            "invalid_state",
            "Mapping is locked after analysis. Upload again to use different mappings.",
        )
    values = [v for v in data.mapping.values() if v]
    if (
        set(data.mapping) - set(FIELDS)
        or set(values) - set(batch.metadata_json["headers"])
        or len(values) != len(set(values))
    ):
        fail("invalid_mapping", "Map each CSV column to at most one supported field.")
    if not any(
        data.mapping.get(k)
        for k in ["primary_email", "alternate_email", "display_name", "first_name", "last_name"]
    ):
        fail("identity_mapping_required", "Map at least one name or email field.")
    batch.mapping = {k: v for k, v in data.mapping.items() if v}
    batch.status = "mapped"
    audit(
        db, user, "import.mapping", batch, "Column mapping saved", after=batch.mapping
    )
    db.commit()
    return serialize(batch)


def reject_row(db, user, row, errors):
    row.validation_errors = errors
    row.resolution = "invalid"
    row.matched_judge_id = None
    audit(db, user, "import.row_rejected", row, f"Rejected import row {row.row_number}",
          after={"batch_id": str(row.batch_id), "row_number": row.row_number, "errors": errors})


def active_contact_identities(db):
    contacts = db.execute(
        select(Judge.id, Judge.display_name, Judge.organization, Judge.primary_email,
               Judge.alternate_email, Judge.preserved_emails)
        .where(Judge.merged_into_id.is_(None))
    ).mappings()
    return {contact["id"]: contact_identity(contact) for contact in contacts}


def recheck_name_matches(db, row, contacts):
    incoming = contact_identity(row.normalized_data)
    matches = {
        judge_id for judge_id, existing in contacts.items()
        if needs_name_review(incoming, existing)
    }
    if not matches:
        return
    # A keep-separate decision covers the matches recorded for this row.
    reviewed = {
        judge_id or prior_judge_id
        for judge_id, prior_judge_id in db.execute(
            select(Duplicate.existing_judge_id, ImportRow.matched_judge_id)
            .outerjoin(ImportRow, ImportRow.id == Duplicate.existing_row_id)
            .where(
                Duplicate.import_row_id == row.id,
                Duplicate.resolution.in_(("create", "dismissed")),
                Duplicate.match_type.in_(("name_only", "name_organization")),
                Duplicate.confidence_score >= 0.7,
            )
        )
    }
    if matches - reviewed:
        fail("name_conflict", "This name now matches another active contact. Reimport the row to review the match.", 409)


@router.post("/imports/{id}/analyze", response_model=ObjectView)
def analyze(id: UUID, user=Depends(require("imports")), db=Depends(get_db)):
    batch = get(db, ImportBatch, id, True)
    if batch.status == "analyzed":
        return serialize(batch)
    if batch.status != "mapped":
        fail("invalid_state", "Save the mapping before analyzing.")
    existing = [
        (judge_id, None, identity)
        for judge_id, identity in active_contact_identities(db).items()
    ]
    for row in db.scalars(
        select(ImportRow).where(ImportRow.batch_id == id).order_by(ImportRow.row_number)
    ):
        data, errors = normalize_row(row.raw_data, batch.mapping)
        row.normalized_data = data
        if errors:
            reject_row(db, user, row, errors)
            continue
        row.resolution = "create"
        identity = contact_identity(data)
        matches = []
        for judge_id, prior_row_id, other in existing:
            match = duplicate_match(identity, other)
            if match:
                matches.append((match, judge_id, prior_row_id))
        for (kind, confidence, explanation), judge_id, prior_row_id in matches:
            db.add(
                Duplicate(
                    import_row_id=row.id,
                    existing_judge_id=judge_id,
                    existing_row_id=prior_row_id,
                    match_type=kind,
                    confidence_score=confidence,
                    explanation=explanation,
                )
            )
        if any(m[0][1] >= 0.7 for m in matches):
            row.resolution = "duplicate_review"
            batch.duplicate_count += 1
        existing.append((None, row.id, identity))
    batch.status = "analyzed"
    audit(
        db,
        user,
        "import.analyzed",
        batch,
        f"Analyzed {batch.total_rows} rows; {batch.duplicate_count} require review",
    )
    db.commit()
    return serialize(batch)


@router.get("/duplicates", response_model=Page)
def duplicates(
    batch_id: UUID | None = None,
    resolution: str = "pending",
    page_no: int = Query(1, alias="page", ge=1),
    page_size: int = Query(25, ge=1, le=100),
    user=Depends(require("imports")),
    db=Depends(get_db),
):
    stmt = (
        select(Duplicate)
        .join(ImportRow, Duplicate.import_row_id == ImportRow.id)
        .order_by(Duplicate.confidence_score.desc(), Duplicate.created_at)
    )
    if resolution:
        stmt = stmt.where(Duplicate.resolution == resolution)
    if batch_id:
        stmt = stmt.where(ImportRow.batch_id == batch_id)
    return page(db, stmt, page_no, page_size)


@router.get("/duplicates/{id}", response_model=ObjectView)
def duplicate(id: UUID, user=Depends(require("imports")), db=Depends(get_db)):
    candidate = get(db, Duplicate, id)
    row = get(db, ImportRow, candidate.import_row_id)
    existing = {}
    if candidate.existing_judge_id:
        existing = judge_views(db, [get(db, Judge, candidate.existing_judge_id)])[0]
    elif candidate.existing_row_id:
        existing = get(db, ImportRow, candidate.existing_row_id).normalized_data
    return {
        **serialize(candidate),
        "incoming": row.normalized_data,
        "existing": existing,
        "batch_id": str(row.batch_id),
        "row_number": row.row_number,
    }


@router.post("/duplicates/{id}/resolve", response_model=ObjectView)
def resolve(
    id: UUID, data: Resolve, user=Depends(require("imports")), db=Depends(get_db)
):
    batch_id = db.scalar(
        select(ImportRow.batch_id).join(Duplicate, Duplicate.import_row_id == ImportRow.id)
        .where(Duplicate.id == id)
    )
    if batch_id is None:
        fail("not_found", "Record not found.", 404)
    batch = get(db, ImportBatch, batch_id, True)
    if batch.status != "analyzed" or not data.confirmed:
        fail(
            "invalid_state", "Confirm decisions before committing the analyzed import."
        )
    candidate = get(db, Duplicate, id)
    row = get(db, ImportRow, candidate.import_row_id)
    if data.decision == "merge":
        if not (candidate.existing_judge_id or candidate.existing_row_id):
            fail("missing_merge_target", "The existing contact is no longer available.", 409)
        if candidate.existing_judge_id:
            target = get(db, Judge, candidate.existing_judge_id)
            if target.merged_into_id:
                fail("invalid_judge", "The merge target changed. Upload the row again to review the surviving contact.", 409)
            ensure_email_ownership(db, contact_addresses(row.normalized_data), target.id)
        for field in data.field_decisions:
            if field not in FIELDS or field in {
                "qualifications",
                "categories",
                "years",
                "notes",
            }:
                fail("invalid_merge_field", "Invalid field selection.")
        row.resolution = "update"
        row.matched_judge_id = candidate.existing_judge_id
        row.field_decisions = {**data.field_decisions, "_candidate": str(candidate.id)}
    else:
        if data.decision == "create":
            ensure_email_ownership(db, contact_addresses(row.normalized_data))
        row.resolution = data.decision
        row.matched_judge_id = None
        row.field_decisions = {}
    for other in db.scalars(select(Duplicate).where(Duplicate.import_row_id == row.id)):
        other.resolution = data.decision if other.id == candidate.id else "dismissed"
    audit(
        db,
        user,
        "duplicate.resolved",
        candidate,
        f"Row {row.row_number}: {data.decision}",
        after=data.model_dump(),
    )
    db.commit()
    return serialize(candidate)


@router.post("/imports/{id}/commit", response_model=ObjectView)
def commit(
    id: UUID, data: Confirm, user=Depends(require("imports")), db=Depends(get_db)
):
    batch = get(db, ImportBatch, id, True)
    if batch.status == "committed":
        return serialize(batch)
    if not data.confirmed or batch.status != "analyzed":
        fail("confirmation_required", "Analyze and confirm the import first.")
    rows = db.scalars(
        select(ImportRow).where(ImportRow.batch_id == id).order_by(ImportRow.row_number)
    ).all()
    if any(r.resolution == "duplicate_review" for r in rows):
        fail(
            "unresolved_duplicates",
            "Review all high-confidence duplicate rows before committing.",
        )
    # Serialize import commits and manual creation against the same advisory lock.
    lock_contact_writes(db)
    contacts = active_contact_identities(db)
    for row in rows:
        if row.resolution in {"invalid", "skip"}:
            batch.skipped_count += 1
            continue
        try:
            with db.begin_nested():
                if row.resolution == "update":
                    candidate = get(
                        db, Duplicate, UUID(row.field_decisions["_candidate"])
                    )
                    target_id = row.matched_judge_id
                    if not target_id and candidate.existing_row_id:
                        target_id = get(
                            db, ImportRow, candidate.existing_row_id
                        ).matched_judge_id
                    if not target_id:
                        fail(
                            "missing_merge_target",
                            "Earlier row was skipped or invalid; select a different merge target.",
                        )
                    target = get(db, Judge, target_id, True)
                    if target.merged_into_id:
                        fail("invalid_judge", "Select the surviving contact before merging the import row.", 409)
                    decisions = {
                        k: v
                        for k, v in row.field_decisions.items()
                        if not k.startswith("_")
                    }
                    merge_data(
                        db, target, row.normalized_data, decisions, user, row=row
                    )
                    row.matched_judge_id = target.id
                else:
                    recheck_name_matches(db, row, contacts)
                    data = {
                        **row.normalized_data,
                        "display_name": contact_name(row.normalized_data["display_name"]),
                    }
                    target = write_judge(
                        db,
                        data,
                        user,
                        source=f"import {batch.id}, row {row.row_number}",
                    )
                    row.matched_judge_id = target.id
            contacts[target.id] = contact_identity(serialize(target))
            if row.resolution == "update":
                batch.updated_count += 1
            else:
                batch.created_count += 1
        except HTTPException as exc:
            if exc.status_code not in {400, 404, 409}:
                raise
            reject_row(db, user, row, [exc.detail["message"]])
            batch.skipped_count += 1
    for candidate, row_resolution in db.execute(
        select(Duplicate, ImportRow.resolution)
        .join(ImportRow, Duplicate.import_row_id == ImportRow.id)
        .where(ImportRow.batch_id == id, Duplicate.resolution == "pending")
    ):
        candidate.resolution = "rejected" if row_resolution == "invalid" else "kept_separate"
    batch.status, batch.completed_at = "committed", now()
    audit(
        db,
        user,
        "import.committed",
        batch,
        f"Created {batch.created_count}, updated {batch.updated_count}, skipped {batch.skipped_count}",
        after=serialize(batch),
    )
    db.commit()
    return serialize(batch)


@router.get("/imports/{id}/errors")
def errors(id: UUID, user=Depends(require("imports")), db=Depends(get_db)):
    batch = get(db, ImportBatch, id)
    rows = db.scalars(
        select(ImportRow)
        .where(ImportRow.batch_id == id, ImportRow.resolution == "invalid")
        .order_by(ImportRow.row_number)
    )
    fields = ["row_number", "errors"]
    columns = {}
    for header in batch.metadata_json["headers"]:
        field = header
        while field in fields or field.startswith(("=", "+", "-", "@", "\t", "\r")):
            field = f"CSV {field}"
        columns[header] = field
        fields.append(field)
    records = [
        {
            **{field: r.raw_data[header] for header, field in columns.items()},
            "row_number": r.row_number,
            "errors": "; ".join(r.validation_errors),
        }
        for r in rows
    ]
    return Response(
        csv_output(records, fields),
        media_type="text/csv",
        headers={"Content-Disposition": "attachment; filename=import-errors.csv"},
    )
