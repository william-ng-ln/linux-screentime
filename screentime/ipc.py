"""Shared IPC constants for screentime daemon ↔ admin GUI communication."""

from pathlib import Path

SOCKET_DIR = Path("/run/screentime")
SOCKET_PATH = SOCKET_DIR / "control.sock"

TOKEN_TTL_SECONDS = 8 * 3600   # session token valid for 8 hours (sliding window)

# Commands that do NOT require an auth token (safe to expose to any user)
READ_COMMANDS = frozenset({
    "authenticate",
    "get_all_apps",
    "get_app",
    "get_today_usage",
    "get_today_usage_including_open",
    "get_usage_history",
    "get_hourly_usage_today",
    "get_daily_usage_for_app",
    "get_setting",
})

# Commands that require a valid session token
WRITE_COMMANDS = frozenset({
    "set_app_allowed",
    "set_app_schedule",
    "set_setting",
    "set_password",
    "scan_apps",
})
