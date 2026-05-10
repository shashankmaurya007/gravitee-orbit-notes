"""Admin endpoints — schedule configuration."""
from __future__ import annotations

from fastapi import APIRouter

from ..scheduler import apply_schedule, load_config, next_run_info, save_config

router = APIRouter(prefix="/api/admin", tags=["admin"])

_DAY_OPTIONS = ["mon", "tue", "wed", "thu", "fri", "sat", "sun"]
_TIMEZONE_OPTIONS = ["UTC", "Europe/London", "America/New_York", "America/Los_Angeles"]


@router.get("/schedule")
async def get_schedule():
    """Return current schedule config plus next-run info."""
    cfg = load_config()
    return {**cfg, **next_run_info(), "day_options": _DAY_OPTIONS, "tz_options": _TIMEZONE_OPTIONS}


@router.post("/schedule")
async def set_schedule(body: dict):
    """Update schedule config and hot-reload the in-process scheduler."""
    cfg = load_config()

    if "enabled"     in body: cfg["enabled"]     = bool(body["enabled"])
    if "day_of_week" in body: cfg["day_of_week"] = str(body["day_of_week"])
    if "hour"        in body: cfg["hour"]         = int(body["hour"])
    if "minute"      in body: cfg["minute"]       = int(body["minute"])
    if "timezone"    in body: cfg["timezone"]     = str(body["timezone"])

    save_config(cfg)
    apply_schedule(cfg)

    return {**cfg, **next_run_info()}
