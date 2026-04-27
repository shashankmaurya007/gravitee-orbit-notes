"""Trigger weekly sync: fetch Orbit data → AI summarize → save drafts."""
from __future__ import annotations

import asyncio
import logging
import uuid
from datetime import datetime, timedelta

from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select

from ..database import get_db
from ..models import NoteStatus, SyncRequest, WeeklyNote
from ..bigquery_client import Customer, build_board_data, fetch_customers
from ..summarizer import generate_company_note
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
        customer.company_name, boards, week_start, week_end
    )

    # generate_company_note returns None when there's no meaningful activity
    if note_data is None:
        logger.info("No meaningful activity for %s — skipping note creation", customer.company_name)
        return

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

    async with AsyncSessionLocal() as db:
        for customer in customers:
            try:
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
