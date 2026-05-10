"""BigQuery-backed replacement for orbit_client.py.

Queries the reporting-299920.gravitee_orbit dataset directly,
preserving the exact Customer / CustomerProject / BoardData / Card / CardComment
interface that summarizer.py and routers/sync.py expect.
"""
from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Optional

from google.cloud import bigquery

from .config import settings

logger = logging.getLogger(__name__)

_DATASET = "reporting-299920.gravitee_orbit"
_RELEVANT_BOARD_TYPES = {"Production", "Onboarding"}


# ── Domain types (identical to orbit_client.py) ───────────────────────────────

@dataclass
class CustomerProject:
    id: str
    customer_id: str
    board_id: str
    board_title: str
    phase_type: str   # "onboarding" | "production" | "unknown"
    status: str


@dataclass
class Customer:
    id: str
    company_name: str
    hubspot_company_id: Optional[str]
    type: str
    projects: list[CustomerProject] = field(default_factory=list)


@dataclass
class CardComment:
    id: str
    text: str
    created_at: datetime


@dataclass
class Card:
    id: str
    title: str
    description: str
    completed: bool
    updated_at: datetime
    list_name: str
    comments: list[CardComment] = field(default_factory=list)


@dataclass
class BoardData:
    board_id: str
    board_title: str
    phase_type: str   # "onboarding" | "production" | "unknown"
    cards: list[Card] = field(default_factory=list)


# ── Helpers ───────────────────────────────────────────────────────────────────

def _bq_client() -> bigquery.Client:
    return bigquery.Client(
        project="reporting-299920",
        credentials=_load_credentials(),
    )


def _load_credentials():
    from google.oauth2 import service_account
    return service_account.Credentials.from_service_account_file(
        settings.google_credentials_file,
        scopes=[
            "https://www.googleapis.com/auth/bigquery",
            "https://www.googleapis.com/auth/cloud-platform",
        ],
    )


def _normalise_board_type(board_type: str | None) -> str:
    bt = (board_type or "").strip()
    if bt == "Onboarding":
        return "onboarding"
    if bt == "Production":
        return "production"
    return "unknown"


def _naive(dt: Any) -> datetime:
    """Strip timezone so comparisons with naive week_start/week_end work."""
    if dt is None:
        return datetime.utcfromtimestamp(0)
    if isinstance(dt, datetime):
        return dt.replace(tzinfo=None)
    return datetime.utcfromtimestamp(0)


# ── Sync BQ helpers (run in executor) ────────────────────────────────────────

def _sync_fetch_customers() -> list[Customer]:
    """
    Pull all customers that have at least one Production or Onboarding board
    and a HubSpot company ID, grouped with their projects.
    """
    client = _bq_client()
    query = f"""
        SELECT
          c.id           AS customer_id,
          c.company_name,
          c.hubspot_company_id,
          c.type,
          cp.id          AS project_id,
          cp.board_id,
          cp.status,
          b.title        AS board_title,
          b.board_type
        FROM `{_DATASET}.customers`         c
        JOIN `{_DATASET}.customer_projects` cp ON cp.customer_id = c.id
        JOIN `{_DATASET}.boards`            b  ON b.id = cp.board_id
        WHERE c.hubspot_company_id IS NOT NULL
          AND b.board_type IN ('Production', 'Onboarding')
        ORDER BY c.company_name, b.board_type
    """
    rows = list(client.query(query).result())
    logger.info("BigQuery returned %d customer-board rows", len(rows))

    customer_map: dict[str, Customer] = {}
    for row in rows:
        cid = row.customer_id
        if cid not in customer_map:
            customer_map[cid] = Customer(
                id=cid,
                company_name=row.company_name,
                hubspot_company_id=row.hubspot_company_id,
                type=row.type or "customer",
            )
        customer_map[cid].projects.append(
            CustomerProject(
                id=row.project_id,
                customer_id=cid,
                board_id=row.board_id,
                board_title=row.board_title or "Unknown Board",
                phase_type=_normalise_board_type(row.board_type),
                status=row.status or "unknown",
            )
        )

    result = list(customer_map.values())
    logger.info("Built %d customers with Production/Onboarding boards", len(result))
    return result


def _sync_fetch_board_data(
    board_id: str,
    board_title: str,
    phase_type: str,
    week_start: datetime,
    week_end: datetime,
) -> Optional[BoardData]:
    """
    Fetch cards for one board that had any activity in [week_start, week_end].
    Activity = card created/updated OR at least one comment created in window.
    """
    client = _bq_client()
    query = f"""
        SELECT
          ca.id          AS card_id,
          ca.title       AS card_title,
          ca.description,
          ca.completed,
          ca.archived,
          ca.updated_at,
          ca.created_at,
          ca.comments,
          l.title        AS list_title
        FROM `{_DATASET}.cards` ca
        LEFT JOIN `{_DATASET}.lists` l ON l.id = ca.list_id
        WHERE ca.board_id = @board_id
          AND (ca.archived IS NULL OR ca.archived = FALSE)
          AND (
            (ca.updated_at BETWEEN @week_start AND @week_end)
            OR (ca.created_at BETWEEN @week_start AND @week_end)
            OR EXISTS (
              SELECT 1 FROM UNNEST(ca.comments) cm
              WHERE cm.created_at BETWEEN @week_start AND @week_end
            )
          )
    """
    job_config = bigquery.QueryJobConfig(
        query_parameters=[
            bigquery.ScalarQueryParameter("board_id",   "STRING",    board_id),
            bigquery.ScalarQueryParameter("week_start", "TIMESTAMP", week_start.replace(tzinfo=timezone.utc)),
            bigquery.ScalarQueryParameter("week_end",   "TIMESTAMP", week_end.replace(tzinfo=timezone.utc)),
        ]
    )
    rows = list(client.query(query, job_config=job_config).result())

    if not rows:
        logger.info("Board '%s' has no activity in window — skipping", board_title)
        return None

    cards: list[Card] = []
    for row in rows:
        # Filter comments to only those within the week window
        comments: list[CardComment] = []
        for cm in (row.comments or []):
            cm_dt = _naive(cm.get("created_at") if isinstance(cm, dict) else getattr(cm, "created_at", None))
            if week_start <= cm_dt <= week_end:
                text = cm.get("text") if isinstance(cm, dict) else getattr(cm, "text", "")
                cid  = cm.get("id")   if isinstance(cm, dict) else getattr(cm, "id",   "")
                comments.append(CardComment(id=cid or "", text=text or "", created_at=cm_dt))

        cards.append(Card(
            id=row.card_id or "",
            title=row.card_title or "",
            description=row.description or "",
            completed=bool(row.completed),
            updated_at=_naive(row.updated_at),
            list_name=row.list_title or "Unknown",
            comments=comments,
        ))

    logger.info("Board '%s': %d active cards in window", board_title, len(cards))
    return BoardData(board_id=board_id, board_title=board_title, phase_type=phase_type, cards=cards)


# ── Async public interface (mirrors orbit_client.py) ─────────────────────────

async def fetch_customers() -> list[Customer]:
    """Async wrapper — runs BQ query in a thread executor."""
    return await asyncio.to_thread(_sync_fetch_customers)


async def build_board_data(
    project: CustomerProject,
    week_start: datetime,
    week_end: datetime,
) -> Optional[BoardData]:
    """Async wrapper — runs BQ query in a thread executor."""
    try:
        return await asyncio.to_thread(
            _sync_fetch_board_data,
            project.board_id,
            project.board_title,
            project.phase_type,
            week_start,
            week_end,
        )
    except Exception:
        logger.exception("Failed to fetch board %s from BigQuery", project.board_id)
        return None
