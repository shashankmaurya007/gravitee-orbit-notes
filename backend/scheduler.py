"""In-process APScheduler — replaces the OS cron job.

Schedule is persisted to schedule_config.json next to pyproject.toml.
Call apply_schedule() after any config change to hot-reload the job.
"""
from __future__ import annotations

import json
import logging
from pathlib import Path

from apscheduler.schedulers.asyncio import AsyncIOScheduler
from apscheduler.triggers.cron import CronTrigger

logger = logging.getLogger(__name__)

# Config file sits at the project root (one level above /backend)
_CONFIG_FILE = Path(__file__).parent.parent / "schedule_config.json"

DEFAULT_CONFIG: dict = {
    "enabled": True,
    "day_of_week": "mon",   # mon | tue | wed | thu | fri | sat | sun
    "hour": 22,
    "minute": 0,
    "timezone": "UTC",
}

scheduler = AsyncIOScheduler()


# ── Config helpers ────────────────────────────────────────────────────────────

def load_config() -> dict:
    if _CONFIG_FILE.exists():
        try:
            return {**DEFAULT_CONFIG, **json.loads(_CONFIG_FILE.read_text())}
        except Exception:
            pass
    return DEFAULT_CONFIG.copy()


def save_config(cfg: dict) -> None:
    _CONFIG_FILE.write_text(json.dumps(cfg, indent=2))


# ── Scheduler helpers ─────────────────────────────────────────────────────────

async def _sync_job() -> None:
    """The actual job that runs on schedule."""
    from .routers.sync import _run_sync
    logger.info("Scheduled sync triggered by APScheduler")
    try:
        result = await _run_sync(week_offset=0)
        logger.info("Scheduled sync complete: %s", result)
    except Exception:
        logger.exception("Scheduled sync failed")


def apply_schedule(cfg: dict | None = None) -> None:
    """Apply (or re-apply) the schedule from config. Safe to call at any time."""
    if cfg is None:
        cfg = load_config()

    scheduler.remove_all_jobs()

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
            misfire_grace_time=3600,  # tolerate up to 1h late start (e.g. server restart)
        )
        logger.info(
            "Sync scheduled: every %s at %02d:%02d %s",
            cfg["day_of_week"].capitalize(), cfg["hour"], cfg["minute"], cfg["timezone"],
        )
    else:
        logger.info("Scheduled sync is disabled")


def next_run_info() -> dict:
    """Return human-readable info about the next scheduled run."""
    job = scheduler.get_job("weekly_sync")
    if not job or not job.next_run_time:
        return {"enabled": False, "next_run": None}
    return {
        "enabled": True,
        "next_run": job.next_run_time.isoformat(),
    }
