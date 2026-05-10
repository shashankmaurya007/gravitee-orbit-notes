"""Async client for the Orbit Kanban Board REST API."""
from __future__ import annotations

import logging
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, Optional

import httpx

from .config import settings

logger = logging.getLogger(__name__)

_HEADERS = {
    "Authorization": f"Bearer {settings.orbit_api_key}",
    "Content-Type": "application/json",
}

# boardType values we care about (others like "Prospect - POC" are skipped)
_RELEVANT_BOARD_TYPES = {"Production", "Onboarding"}


# ── Domain types ──────────────────────────────────────────────────────────────

@dataclass
class CustomerProject:
    id: str
    customer_id: str
    board_id: str
    board_title: str   # pre-fetched from boards list
    phase_type: str    # normalised: "onboarding" | "production" | "unknown"
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

def _parse_dt(value: Any) -> datetime:
    if isinstance(value, datetime):
        return value
    if not value:
        return datetime.utcfromtimestamp(0)
    try:
        return datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except Exception:
        return datetime.utcfromtimestamp(0)


def _normalise_board_type(board_type: str | None) -> str:
    """Map Orbit boardType values to internal phase labels."""
    bt = (board_type or "").strip()
    if bt == "Onboarding":
        return "onboarding"
    if bt == "Production":
        return "production"
    return "unknown"


# ── API calls ─────────────────────────────────────────────────────────────────

async def fetch_customers() -> list[Customer]:
    """
    Build a list of Customers with their relevant boards attached.

    Strategy:
    1. GET /api/orbit/customers  → company_name → hubspot_company_id map
    2. GET /api/orbit/boards     → boards with customerName + boardType
    3. Join: attach boards to matching customers
    """
    async with httpx.AsyncClient(timeout=30) as client:
        customers_resp, boards_resp = await _fetch_both(client)

    # Build lookup: company_name → Customer (deduplicated by name, prefer one with HS ID)
    customer_map: dict[str, Customer] = {}
    for c in customers_resp:
        name = c["company_name"]
        existing = customer_map.get(name)
        candidate = Customer(
            id=c["id"],
            company_name=name,
            hubspot_company_id=c.get("hubspot_company_id"),
            type=c.get("type", "customer"),
        )
        # Keep the one with a HubSpot ID, or first seen
        if existing is None or (candidate.hubspot_company_id and not existing.hubspot_company_id):
            customer_map[name] = candidate

    # Attach boards to customers
    for board in boards_resp:
        customer_name = board.get("customerName")
        board_type = board.get("boardType", "")
        if not customer_name or board_type not in _RELEVANT_BOARD_TYPES:
            continue

        customer = customer_map.get(customer_name)
        if not customer:
            logger.debug("Board '%s' references unknown customer '%s'", board.get("title"), customer_name)
            continue

        project = CustomerProject(
            id=board["id"],
            customer_id=customer.id,
            board_id=board["id"],
            board_title=board.get("title", "Unknown Board"),
            phase_type=_normalise_board_type(board_type),
            status="in_progress",
        )
        customer.projects.append(project)

    result = [c for c in customer_map.values() if c.projects]
    logger.info("Found %d customers with active boards", len(result))
    return result


async def _fetch_both(client: httpx.AsyncClient):
    """Fetch customers and boards in parallel."""
    import asyncio
    customers_task = client.get(
        f"{settings.orbit_base_url}/api/orbit/customers",
        headers=_HEADERS,
        params={"type": "all"},
    )
    boards_task = client.get(
        f"{settings.orbit_base_url}/api/orbit/boards",
        headers=_HEADERS,
    )
    customers_resp, boards_resp = await asyncio.gather(customers_task, boards_task)
    customers_resp.raise_for_status()
    boards_resp.raise_for_status()
    return customers_resp.json(), boards_resp.json()


async def fetch_board_state(board_id: str) -> tuple[dict, dict]:
    """Return (lists_by_id, cards_by_id) for a board."""
    url = f"{settings.orbit_base_url}/api/orbit/state/board/{board_id}"
    async with httpx.AsyncClient(timeout=60) as client:
        resp = await client.get(url, headers=_HEADERS)
        resp.raise_for_status()
        data = resp.json()
    return data.get("lists", {}), data.get("cards", {})


# ── Card filtering ────────────────────────────────────────────────────────────

def _extract_cards(
    lists_raw: dict,
    cards_raw: dict,
    week_start: datetime,
    week_end: datetime,
) -> list[Card]:
    """Filter cards to those active in the reporting window."""
    list_name_by_id: dict[str, str] = {
        lid: ldata.get("title", "Unknown") for lid, ldata in lists_raw.items()
    }

    cards: list[Card] = []
    for card_id, c in cards_raw.items():
        if c.get("archived"):
            continue

        updated_at = _parse_dt(c.get("updatedAt"))
        comments_raw: list[dict] = c.get("comments") or []

        comments_in_window = [
            CardComment(
                id=cm.get("id", ""),
                text=cm.get("text", ""),
                created_at=_parse_dt(cm.get("createdAt")),
            )
            for cm in comments_raw
            if week_start <= _parse_dt(cm.get("createdAt")).replace(tzinfo=None) <= week_end
        ]

        card_updated_in_window = week_start <= updated_at.replace(tzinfo=None) <= week_end

        if not card_updated_in_window and not comments_in_window:
            continue

        cards.append(
            Card(
                id=card_id,
                title=c.get("title", ""),
                description=c.get("description", ""),
                completed=c.get("completed", False),
                updated_at=updated_at,
                list_name=list_name_by_id.get(c.get("listId", ""), "Unknown"),
                comments=comments_in_window,
            )
        )
    return cards


async def build_board_data(
    project: CustomerProject,
    week_start: datetime,
    week_end: datetime,
) -> Optional[BoardData]:
    """Fetch and filter one board's data for the reporting window."""
    try:
        lists_raw, cards_raw = await fetch_board_state(project.board_id)
        cards = _extract_cards(lists_raw, cards_raw, week_start, week_end)

        if not cards:
            logger.info("Board '%s' has no activity in window — skipping", project.board_title)
            return None

        return BoardData(
            board_id=project.board_id,
            board_title=project.board_title,
            phase_type=project.phase_type,
            cards=cards,
        )
    except httpx.HTTPStatusError as exc:
        logger.warning("HTTP %s for board %s: %s", exc.response.status_code, project.board_id, exc)
        return None
    except Exception:
        logger.exception("Failed to fetch board %s", project.board_id)
        return None
