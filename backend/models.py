import enum
import uuid
from datetime import datetime

from sqlalchemy import Column, DateTime, Enum, String, Text
from pydantic import BaseModel, ConfigDict
from typing import Optional

from .database import Base


class NoteStatus(str, enum.Enum):
    draft = "draft"
    pushed = "pushed"
    failed = "failed"


class WeeklyNote(Base):
    __tablename__ = "weekly_notes"

    id = Column(String, primary_key=True, default=lambda: str(uuid.uuid4()))
    company_id = Column(String, nullable=False, index=True)
    company_name = Column(String, nullable=False)
    hubspot_company_id = Column(String, nullable=True)
    week_start = Column(DateTime, nullable=False)
    week_end = Column(DateTime, nullable=False)
    onboarding_summary = Column(Text, nullable=True)
    production_summary = Column(Text, nullable=True)
    risks_blockers = Column(Text, nullable=True)
    note_body = Column(Text, nullable=False)
    status = Column(Enum(NoteStatus), default=NoteStatus.draft, nullable=False)
    hubspot_note_id = Column(String, nullable=True)
    error_message = Column(Text, nullable=True)
    csm = Column(String, nullable=True)
    tam = Column(String, nullable=True)
    pod = Column(String, nullable=True)
    ae_name = Column(String, nullable=True)
    # significant | moderate | none
    activity_level = Column(String, nullable=True, default="moderate")
    created_at = Column(DateTime, default=datetime.utcnow)
    pushed_at = Column(DateTime, nullable=True)


# ── Pydantic schemas ──────────────────────────────────────────────────────────

class NoteOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: str
    company_id: str
    company_name: str
    hubspot_company_id: Optional[str]
    week_start: datetime
    week_end: datetime
    onboarding_summary: Optional[str]
    production_summary: Optional[str]
    risks_blockers: Optional[str]
    note_body: str
    status: NoteStatus
    hubspot_note_id: Optional[str]
    error_message: Optional[str]
    csm: Optional[str]
    tam: Optional[str]
    pod: Optional[str]
    ae_name: Optional[str]
    activity_level: Optional[str]
    created_at: datetime
    pushed_at: Optional[datetime]


class SyncRequest(BaseModel):
    week_offset: int = 0
    limit: int | None = None  # cap number of companies processed (None = all)


class PushRequest(BaseModel):
    note_ids: list[str]
