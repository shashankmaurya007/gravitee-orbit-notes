"""Trigger weekly sync: fetch Orbit data → AI summarize → save drafts."""
from __future__ import annotations

import asyncio
import logging
import uuid
from datetime import datetime, timedelta

from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select

from ..database import get_db
from ..models import NoteStatus, SyncRequest, WeeklyNote
from ..bigquery_client import Customer, build_board_data, fetch_customers
from ..summarizer import classify_boards_activity, generate_company_note
from ..hubspot_client import fetch_all_owners, fetch_company_fields

router = APIRouter(prefix="/api/sync", tags=["sync"])
logger = logging.getLogger(__name__)


def _week_window(offset: int = 0) -> tuple[datetime, datetime]:
    """
    offset=0 → last full Mon–Sun
    offset=1 → two weeks ago, etc.
    """
    today = datetime.utcnow().date()
    # days_since_monday: Monday=0 … Sunday=6
    days_since_monday = today.weekday()
    last_monday = today - timedelta(days=days_since_monday + 7 + offset * 7)
    last_sunday = last_monday + timedelta(days=6)
    week_start = datetime(last_monday.year, last_monday.month, last_monday.day, 0, 0, 0)
    week_end = datetime(last_sunday.year, last_sunday.month, last_sunday.day, 23, 59, 59)
    return week_start, week_end


async def _process_customer(
    customer: Customer,
    week_start: datetime,
    week_end: datetime,
    db: AsyncSession,
    owner_map: dict,
) -> None:
    """Build boards, summarize, upsert WeeklyNote for one customer."""
    if not customer.projects:
        logger.info("Customer %s has no projects — skipping", customer.company_name)
        return

    # Skip unmapped (no HubSpot ID)
    if not customer.hubspot_company_id:
        logger.info("Customer %s has no hubspot_company_id — skipping", customer.company_name)
        return

    # Fetch board data concurrently
    board_tasks = [
        build_board_data(project, week_start, week_end)
        for project in customer.projects
    ]
    boards_or_none = await asyncio.gather(*board_tasks, return_exceptions=False)
    boards = [b for b in boards_or_none if b is not None]

    if not boards:
        logger.info("No active boards for %s this week", customer.company_name)
        return

    # ── Pre-Gemini classification ──────────────────────────────────────────
    # Classify activity level from raw board data before making any AI call.
    # "none" accounts are skipped entirely — no Gemini call, no note created.
    activity_level = classify_boards_activity(boards)
    if activity_level == "none":
        logger.info(
            "%s: no meaningful card activity (no comments, no completions, no updates) — skipping AI call",
            customer.company_name,
        )
        return
    logger.info(
        "%s: classified as '%s' — sending to Gemini",
        customer.company_name, activity_level,
    )

    # Idempotency: skip if a note already exists for this company+week
    existing = await db.scalar(
        select(WeeklyNote).where(
            WeeklyNote.company_id == customer.id,
            WeeklyNote.week_start == week_start,
        )
    )
    if existing:
        logger.info("Note already exists for %s / %s — skipping", customer.company_name, week_start.date())
        return

    note_data = await generate_company_note(
        customer.company_name, boards, week_start, week_end, activity_level
    )

    hs_fields = await fetch_company_fields(customer.hubspot_company_id, owner_map)

    note = WeeklyNote(
        id=str(uuid.uuid4()),
        company_id=customer.id,
        company_name=customer.company_name,
        hubspot_company_id=customer.hubspot_company_id,
        week_start=week_start,
        week_end=week_end,
        onboarding_summary=note_data.onboarding_summary,
        production_summary=note_data.production_summary,
        risks_blockers=note_data.risks_blockers,
        note_body=note_data.note_body,
        status=NoteStatus.draft,
        csm=hs_fields.get("csm"),
        tam=hs_fields.get("tam"),
        pod=hs_fields.get("pod"),
        ae_name=hs_fields.get("ae_name"),
        activity_level=note_data.activity_level,
        error_message=note_data.error_message,
    )
    db.add(note)
    await db.commit()
    logger.info("Saved draft note for %s", customer.company_name)


async def _run_sync(week_offset: int, limit: int | None = None) -> dict:
    from ..database import AsyncSessionLocal

    week_start, week_end = _week_window(week_offset)
    logger.info("Syncing window %s → %s", week_start.date(), week_end.date())

    customers = await fetch_customers()
    logger.info("Found %d customers", len(customers))

    owner_map = await fetch_all_owners()
    logger.info("Loaded %d HubSpot owners", len(owner_map))

    if limit:
        customers = customers[:limit]
        logger.info("Limiting to %d customers", limit)

    results = {"processed": 0, "skipped": 0, "errors": 0}

    # Each customer gets its own session so a single failure cannot
    # invalidate the session and cascade errors to all subsequent customers.
    for customer in customers:
        try:
            async with AsyncSessionLocal() as db:
                await _process_customer(customer, week_start, week_end, db, owner_map)
            results["processed"] += 1
        except Exception:
            logger.exception("Failed processing customer %s", customer.company_name)
            results["errors"] += 1

    return {**results, "week_start": str(week_start.date()), "week_end": str(week_end.date())}


@router.post("")
async def trigger_sync(
    body: SyncRequest,
    background_tasks: BackgroundTasks,
):
    """
    Trigger a sync in the background. Returns immediately.
    Poll GET /api/notes to see results as they arrive.
    """
    background_tasks.add_task(_run_sync, body.week_offset, body.limit)
    week_start, week_end = _week_window(body.week_offset)
    return {
        "status": "started",
        "week_start": str(week_start.date()),
        "week_end": str(week_end.date()),
        "message": "Sync running in background — refresh the notes table in a few seconds.",
    }


@router.post("/run")
async def trigger_sync_blocking(body: SyncRequest):
    """Blocking version of sync — waits for completion and returns results."""
    result = await _run_sync(body.week_offset, body.limit)
    return result


class CompanySyncRequest(BaseModel):
    hubspot_company_id: str
    week_offset: int = 0


@router.post("/company")
async def sync_company(body: CompanySyncRequest, background_tasks: BackgroundTasks):
    """
    Trigger note generation for a single company by HubSpot company ID.
    Returns immediately; poll GET /api/notes/company/{id} until the note appears.
    """
    background_tasks.add_task(_run_company_sync, body.hubspot_company_id, body.week_offset)
    week_start, week_end = _week_window(body.week_offset)
    return {
        "status": "started",
        "week_start": str(week_start.date()),
        "week_end": str(week_end.date()),
    }


async def _run_company_sync(hubspot_company_id: str, week_offset: int) -> None:
    from ..database import AsyncSessionLocal

    week_start, week_end = _week_window(week_offset)
    logger.info(
        "Company sync: %s for %s → %s",
        hubspot_company_id, week_start.date(), week_end.date(),
    )

    customers = await fetch_customers()
    customer = next(
        (c for c in customers if c.hubspot_company_id == hubspot_company_id), None
    )

    if not customer:
        logger.warning("No customer found with hubspot_company_id=%s", hubspot_company_id)
        return

    owner_map = await fetch_all_owners()

    async with AsyncSessionLocal() as db:
        await _process_customer(customer, week_start, week_end, db, owner_map)
