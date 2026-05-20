"""In-process APScheduler — replaces the OS cron job.

Schedule configs are persisted to JSON files next to pyproject.toml.
Call apply_schedule() / apply_push_schedule() after any config change.
"""
from __future__ import annotations

import json
import logging
from pathlib import Path

from apscheduler.schedulers.asyncio import AsyncIOScheduler
from apscheduler.triggers.cron import CronTrigger

logger = logging.getLogger(__name__)

_ROOT = Path(__file__).parent.parent
_CONFIG_FILE      = _ROOT / "schedule_config.json"
_PUSH_CONFIG_FILE = _ROOT / "push_schedule_config.json"

DEFAULT_CONFIG: dict = {
    "enabled": True,
    "day_of_week": "mon",
    "hour": 22,
    "minute": 0,
    "timezone": "UTC",
}

DEFAULT_PUSH_CONFIG: dict = {
    "enabled": False,
    "day_of_week": "mon",
    "hour": 23,
    "minute": 0,
    "timezone": "UTC",
    "activity_levels": ["significant"],   # which activity levels to auto-push
}

scheduler = AsyncIOScheduler()


# ── Sync config helpers ───────────────────────────────────────────────────────

def load_config() -> dict:
    if _CONFIG_FILE.exists():
        try:
            return {**DEFAULT_CONFIG, **json.loads(_CONFIG_FILE.read_text())}
        except Exception:
            pass
    return DEFAULT_CONFIG.copy()


def save_config(cfg: dict) -> None:
    _CONFIG_FILE.write_text(json.dumps(cfg, indent=2))


# ── Push config helpers ───────────────────────────────────────────────────────

def load_push_config() -> dict:
    if _PUSH_CONFIG_FILE.exists():
        try:
            return {**DEFAULT_PUSH_CONFIG, **json.loads(_PUSH_CONFIG_FILE.read_text())}
        except Exception:
            pass
    return DEFAULT_PUSH_CONFIG.copy()


def save_push_config(cfg: dict) -> None:
    _PUSH_CONFIG_FILE.write_text(json.dumps(cfg, indent=2))


# ── Sync job ──────────────────────────────────────────────────────────────────

async def _sync_job() -> None:
    from .routers.sync import _run_sync
    logger.info("Scheduled sync triggered by APScheduler")
    try:
        result = await _run_sync(week_offset=0)
        logger.info("Scheduled sync complete: %s", result)
    except Exception:
        logger.exception("Scheduled sync failed")


def apply_schedule(cfg: dict | None = None) -> None:
    """Apply (or re-apply) the sync schedule. Safe to call at any time."""
    if cfg is None:
        cfg = load_config()

    job = scheduler.get_job("weekly_sync")
    if job:
        scheduler.remove_job("weekly_sync")

    if cfg.get("enabled"):
        scheduler.add_job(
            _sync_job,
            CronTrigger(
                day_of_week=cfg["day_of_week"],
                hour=int(cfg["hour"]),
                minute=int(cfg["minute"]),
                timezone=cfg["timezone"],
            ),
            id="weekly_sync",
            replace_existing=True,
            misfire_grace_time=3600,
        )
        logger.info(
            "Sync scheduled: every %s at %02d:%02d %s",
            cfg["day_of_week"].capitalize(), cfg["hour"], cfg["minute"], cfg["timezone"],
        )
    else:
        logger.info("Scheduled sync is disabled")


def next_run_info() -> dict:
    job = scheduler.get_job("weekly_sync")
    if not job or not job.next_run_time:
        return {"enabled": False, "next_run": None}
    return {"enabled": True, "next_run": job.next_run_time.isoformat()}


# ── Push job ──────────────────────────────────────────────────────────────────

async def _push_job() -> None:
    from .database import AsyncSessionLocal
    from .routers.notes import _push_all_drafts
    cfg = load_push_config()
    activity_levels = cfg.get("activity_levels") or ["significant"]
    logger.info("Scheduled push triggered — activity levels: %s", activity_levels)
    try:
        async with AsyncSessionLocal() as db:
            result = await _push_all_drafts(db, activity_levels=activity_levels)
        logger.info("Scheduled push complete: %s", result)
    except Exception:
        logger.exception("Scheduled push failed")


def apply_push_schedule(cfg: dict | None = None) -> None:
    """Apply (or re-apply) the push schedule. Safe to call at any time."""
    if cfg is None:
        cfg = load_push_config()

    job = scheduler.get_job("weekly_push")
    if job:
        scheduler.remove_job("weekly_push")

    if cfg.get("enabled"):
        scheduler.add_job(
            _push_job,
            CronTrigger(
                day_of_week=cfg["day_of_week"],
                hour=int(cfg["hour"]),
                minute=int(cfg["minute"]),
                timezone=cfg["timezone"],
            ),
            id="weekly_push",
            replace_existing=True,
            misfire_grace_time=3600,
        )
        logger.info(
            "Push scheduled: every %s at %02d:%02d %s",
            cfg["day_of_week"].capitalize(), cfg["hour"], cfg["minute"], cfg["timezone"],
        )
    else:
        logger.info("Scheduled push is disabled")


def next_push_run_info() -> dict:
    job = scheduler.get_job("weekly_push")
    if not job or not job.next_run_time:
        return {"enabled": False, "next_run": None}
    return {"enabled": True, "next_run": job.next_run_time.isoformat()}
