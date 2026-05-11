"""Admin endpoints — schedule configuration."""
from __future__ import annotations

from fastapi import APIRouter

from ..scheduler import (
    apply_push_schedule,
    apply_schedule,
    load_config,
    load_push_config,
    next_push_run_info,
    next_run_info,
    save_config,
    save_push_config,
)

router = APIRouter(prefix="/api/admin", tags=["admin"])

_DAY_OPTIONS = ["mon", "tue", "wed", "thu", "fri", "sat", "sun"]
_TIMEZONE_OPTIONS = ["UTC", "Europe/London", "America/New_York", "America/Los_Angeles"]


@router.get("/schedule")
async def get_schedule():
    cfg = load_config()
    return {**cfg, **next_run_info(), "day_options": _DAY_OPTIONS, "tz_options": _TIMEZONE_OPTIONS}


@router.post("/schedule")
async def set_schedule(body: dict):
    cfg = load_config()
    if "enabled"     in body: cfg["enabled"]     = bool(body["enabled"])
    if "day_of_week" in body: cfg["day_of_week"] = str(body["day_of_week"])
    if "hour"        in body: cfg["hour"]         = int(body["hour"])
    if "minute"      in body: cfg["minute"]       = int(body["minute"])
    if "timezone"    in body: cfg["timezone"]     = str(body["timezone"])
    save_config(cfg)
    apply_schedule(cfg)
    return {**cfg, **next_run_info()}


@router.get("/push-schedule")
async def get_push_schedule():
    cfg = load_push_config()
    return {**cfg, **next_push_run_info(), "day_options": _DAY_OPTIONS, "tz_options": _TIMEZONE_OPTIONS}


@router.post("/push-schedule")
async def set_push_schedule(body: dict):
    cfg = load_push_config()
    if "enabled"     in body: cfg["enabled"]     = bool(body["enabled"])
    if "day_of_week" in body: cfg["day_of_week"] = str(body["day_of_week"])
    if "hour"        in body: cfg["hour"]         = int(body["hour"])
    if "minute"      in body: cfg["minute"]       = int(body["minute"])
    if "timezone"    in body: cfg["timezone"]     = str(body["timezone"])
    save_push_config(cfg)
    apply_push_schedule(cfg)
    return {**cfg, **next_push_run_info()}
