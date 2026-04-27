"""CRUD + push endpoints for WeeklyNote records."""
from __future__ import annotations

import logging
from datetime import datetime

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import select, desc, distinct
from sqlalchemy.ext.asyncio import AsyncSession

from ..database import get_db
from ..hubspot_client import create_note
from ..models import NoteOut, NoteStatus, PushRequest, WeeklyNote

router = APIRouter(prefix="/api/notes", tags=["notes"])
logger = logging.getLogger(__name__)


@router.get("", response_model=list[NoteOut])
async def list_notes(
    week_start: str | None = None,
    db: AsyncSession = Depends(get_db),
):
    """List all notes, optionally filtered by week_start (YYYY-MM-DD)."""
    stmt = select(WeeklyNote).order_by(desc(WeeklyNote.created_at))
    if week_start:
        try:
            dt = datetime.strptime(week_start, "%Y-%m-%d")
            stmt = stmt.where(WeeklyNote.week_start == dt)
        except ValueError:
            raise HTTPException(400, "week_start must be YYYY-MM-DD")
    result = await db.execute(stmt)
    return result.scalars().all()


@router.get("/filters")
async def get_filters(db: AsyncSession = Depends(get_db)):
    """Return distinct values for CSM, TAM, Pod, AE filters."""
    async def distinct_values(col):
        result = await db.execute(select(distinct(col)).where(col.isnot(None)))
        return sorted(v for (v,) in result.all() if v)

    return {
        "csm":  await distinct_values(WeeklyNote.csm),
        "tam":  await distinct_values(WeeklyNote.tam),
        "pod":  await distinct_values(WeeklyNote.pod),
        "ae":   await distinct_values(WeeklyNote.ae_name),
    }


@router.get("/{note_id}", response_model=NoteOut)
async def get_note(note_id: str, db: AsyncSession = Depends(get_db)):
    note = await db.get(WeeklyNote, note_id)
    if not note:
        raise HTTPException(404, "Note not found")
    return note


@router.patch("/{note_id}/body")
async def update_note_body(
    note_id: str,
    payload: dict,
    db: AsyncSession = Depends(get_db),
):
    """Allow user to edit the note body before pushing."""
    note = await db.get(WeeklyNote, note_id)
    if not note:
        raise HTTPException(404, "Note not found")
    if note.status == NoteStatus.pushed:
        raise HTTPException(400, "Cannot edit a note that has already been pushed")
    if "note_body" not in payload:
        raise HTTPException(400, "Payload must contain 'note_body'")
    note.note_body = payload["note_body"]
    await db.commit()
    return {"success": True}


@router.post("/push")
async def push_notes(
    body: PushRequest,
    db: AsyncSession = Depends(get_db),
):
    """Push selected notes to HubSpot. Returns per-note result."""
    results = []
    for note_id in body.note_ids:
        note = await db.get(WeeklyNote, note_id)
        if not note:
            results.append({"id": note_id, "status": "error", "message": "Not found"})
            continue
        if note.status == NoteStatus.pushed:
            results.append({"id": note_id, "status": "skipped", "message": "Already pushed"})
            continue
        if not note.hubspot_company_id:
            note.status = NoteStatus.failed
            note.error_message = "No HubSpot company ID"
            await db.commit()
            results.append({"id": note_id, "status": "error", "message": "No HubSpot company ID"})
            continue
        try:
            hs_note_id = await create_note(note.hubspot_company_id, note.note_body)
            note.status = NoteStatus.pushed
            note.hubspot_note_id = hs_note_id
            note.pushed_at = datetime.utcnow()
            note.error_message = None
            await db.commit()
            results.append({"id": note_id, "status": "pushed", "hubspot_note_id": hs_note_id})
        except Exception as exc:
            logger.exception("Push failed for note %s", note_id)
            note.status = NoteStatus.failed
            note.error_message = str(exc)
            await db.commit()
            results.append({"id": note_id, "status": "error", "message": str(exc)})

    return {"results": results}


@router.delete("/{note_id}")
async def delete_note(note_id: str, db: AsyncSession = Depends(get_db)):
    note = await db.get(WeeklyNote, note_id)
    if not note:
        raise HTTPException(404, "Note not found")
    await db.delete(note)
    await db.commit()
    return {"success": True}
