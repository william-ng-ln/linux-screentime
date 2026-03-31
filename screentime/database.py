import sqlite3
import hashlib
import os
import time
from pathlib import Path
from dataclasses import dataclass, field
from typing import Optional
from datetime import date


@dataclass
class AppRecord:
    desktop_id: str
    name: str
    exec_binary: str
    icon: str
    categories: str
    allowed: bool
    daily_limit_minutes: int
    id: Optional[int] = None
    exec_args: str = ""         # Extra args for matching apps that share a binary (e.g. waydroid package)
    limit_schedule: str = ""    # Mon-Sun limits as "30,30,30,30,30,60,60", "" = use daily_limit_minutes


@dataclass
class UsageSession:
    desktop_id: str
    session_date: str
    pid: int
    started_at: float
    ended_at: Optional[float]
    duration_seconds: Optional[float]


class Database:
    def __init__(self, path: Path):
        self.path = path
        self.path.parent.mkdir(parents=True, exist_ok=True)

    def _connect(self) -> sqlite3.Connection:
        conn = sqlite3.connect(str(self.path))
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA foreign_keys=ON")
        return conn

    def _fix_permissions(self):
        """Ensure DB file and directory are writable by all users on this machine."""
        try:
            self.path.chmod(0o666)
            self.path.parent.chmod(0o777)
        except PermissionError:
            pass  # Already set correctly by whoever owns it

    def initialize_schema(self):
        with self._connect() as conn:
            conn.executescript("""
                CREATE TABLE IF NOT EXISTS applications (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    desktop_id TEXT UNIQUE NOT NULL,
                    name TEXT NOT NULL,
                    exec_binary TEXT NOT NULL,
                    icon TEXT DEFAULT '',
                    categories TEXT DEFAULT '',
                    allowed INTEGER DEFAULT 0,
                    daily_limit_minutes INTEGER DEFAULT 0,
                    exec_args TEXT DEFAULT '',
                    limit_schedule TEXT DEFAULT ''
                );

                CREATE TABLE IF NOT EXISTS usage_sessions (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    desktop_id TEXT NOT NULL,
                    session_date TEXT NOT NULL,
                    pid INTEGER NOT NULL,
                    started_at REAL NOT NULL,
                    ended_at REAL,
                    duration_seconds REAL
                );

                CREATE TABLE IF NOT EXISTS settings (
                    key TEXT PRIMARY KEY,
                    value TEXT
                );
            """)
            # Default settings
            defaults = {
                "admin_password_hash": _hash_password("admin"),
                "enabled": "0",   # disabled until parent configures
                "default_allow": "0",
                "kid_user": "",
            }
            for k, v in defaults.items():
                conn.execute(
                    "INSERT OR IGNORE INTO settings (key, value) VALUES (?, ?)",
                    (k, v)
                )
        # Migrate existing DBs that predate new columns
        with self._connect() as conn:
            for col, definition in [("exec_args", "TEXT DEFAULT ''"),
                                     ("limit_schedule", "TEXT DEFAULT ''")]:
                try:
                    conn.execute(f"ALTER TABLE applications ADD COLUMN {col} {definition}")
                except Exception:
                    pass  # Column already exists
        self._fix_permissions()

    def upsert_application(self, app: AppRecord):
        with self._connect() as conn:
            default_allowed = self.get_setting("default_allow") == "1"
            conn.execute("""
                INSERT INTO applications
                    (desktop_id, name, exec_binary, icon, categories, allowed, daily_limit_minutes, exec_args)
                VALUES (?, ?, ?, ?, ?, ?, 0, ?)
                ON CONFLICT(desktop_id) DO UPDATE SET
                    name=excluded.name,
                    exec_binary=excluded.exec_binary,
                    icon=excluded.icon,
                    categories=excluded.categories,
                    exec_args=excluded.exec_args
            """, (
                app.desktop_id, app.name, app.exec_binary,
                app.icon, app.categories, 1 if default_allowed else 0,
                app.exec_args,
            ))

    def get_all_apps(self) -> list[AppRecord]:
        with self._connect() as conn:
            rows = conn.execute(
                "SELECT * FROM applications ORDER BY name"
            ).fetchall()
            return [_row_to_app(r) for r in rows]

    def get_app(self, desktop_id: str) -> Optional[AppRecord]:
        with self._connect() as conn:
            row = conn.execute(
                "SELECT * FROM applications WHERE desktop_id=?", (desktop_id,)
            ).fetchone()
            return _row_to_app(row) if row else None

    def set_app_allowed(self, desktop_id: str, allowed: bool):
        with self._connect() as conn:
            conn.execute(
                "UPDATE applications SET allowed=? WHERE desktop_id=?",
                (1 if allowed else 0, desktop_id)
            )

    def set_app_limit(self, desktop_id: str, minutes: int):
        with self._connect() as conn:
            conn.execute(
                "UPDATE applications SET daily_limit_minutes=? WHERE desktop_id=?",
                (minutes, desktop_id)
            )

    def update_app(self, desktop_id: str, allowed: bool, daily_limit_minutes: int,
                   limit_schedule: str = ""):
        with self._connect() as conn:
            conn.execute(
                "UPDATE applications SET allowed=?, daily_limit_minutes=?, limit_schedule=? WHERE desktop_id=?",
                (1 if allowed else 0, daily_limit_minutes, limit_schedule, desktop_id)
            )

    def set_app_schedule(self, desktop_id: str, daily_limit_minutes: int, limit_schedule: str):
        with self._connect() as conn:
            conn.execute(
                "UPDATE applications SET daily_limit_minutes=?, limit_schedule=? WHERE desktop_id=?",
                (daily_limit_minutes, limit_schedule, desktop_id)
            )

    def get_cmdline_apps(self) -> list[AppRecord]:
        """Return apps that need cmdline matching (exec_args is non-empty)."""
        apps = self.get_all_apps()
        return [a for a in apps if a.exec_args]

    def remove_unlisted_apps(self, current_desktop_ids: set[str]):
        """Remove apps from DB that are no longer found on disk."""
        if not current_desktop_ids:
            return
        placeholders = ",".join("?" * len(current_desktop_ids))
        with self._connect() as conn:
            conn.execute(
                f"DELETE FROM applications WHERE desktop_id NOT IN ({placeholders})",
                list(current_desktop_ids)
            )

    def get_exec_binaries(self) -> dict[str, AppRecord]:
        """Returns {exec_binary: AppRecord} for all apps."""
        apps = self.get_all_apps()
        return {a.exec_binary: a for a in apps if a.exec_binary}

    def close_stale_sessions(self):
        """Close any sessions left open from a previous daemon run.

        We don't know the real end time, so we close each session at
        started_at + whatever time had elapsed — capped at 24 hours to
        avoid inflating history from very old stale rows.
        """
        now = time.time()
        max_duration = 24 * 3600
        with self._connect() as conn:
            rows = conn.execute(
                "SELECT id, started_at FROM usage_sessions WHERE ended_at IS NULL"
            ).fetchall()
            for row in rows:
                duration = min(now - row["started_at"], max_duration)
                ended_at = row["started_at"] + duration
                conn.execute(
                    "UPDATE usage_sessions SET ended_at=?, duration_seconds=? WHERE id=?",
                    (ended_at, duration, row["id"])
                )

    def open_session(self, desktop_id: str, pid: int, started_at: float):
        with self._connect() as conn:
            conn.execute("""
                INSERT OR IGNORE INTO usage_sessions
                    (desktop_id, session_date, pid, started_at)
                VALUES (?, ?, ?, ?)
            """, (desktop_id, date.today().isoformat(), pid, started_at))

    def close_session(self, pid: int, ended_at: float):
        with self._connect() as conn:
            row = conn.execute(
                "SELECT id, started_at FROM usage_sessions WHERE pid=? AND ended_at IS NULL",
                (pid,)
            ).fetchone()
            if row:
                duration = ended_at - row["started_at"]
                conn.execute("""
                    UPDATE usage_sessions
                    SET ended_at=?, duration_seconds=?
                    WHERE id=?
                """, (ended_at, duration, row["id"]))

    def get_today_usage(self) -> dict[str, float]:
        """Returns {desktop_id: total_seconds_today} (closed sessions only)."""
        today = date.today().isoformat()
        with self._connect() as conn:
            rows = conn.execute("""
                SELECT desktop_id, SUM(duration_seconds) as total
                FROM usage_sessions
                WHERE session_date=? AND duration_seconds IS NOT NULL
                GROUP BY desktop_id
            """, (today,)).fetchall()
            return {r["desktop_id"]: r["total"] for r in rows}

    def get_today_usage_including_open(self) -> dict[str, float]:
        """Returns {desktop_id: total_seconds_today} including still-open sessions."""
        today = date.today().isoformat()
        now = time.time()
        with self._connect() as conn:
            closed = conn.execute("""
                SELECT desktop_id, SUM(duration_seconds) as total
                FROM usage_sessions
                WHERE session_date=? AND duration_seconds IS NOT NULL
                GROUP BY desktop_id
            """, (today,)).fetchall()
            result = {r["desktop_id"]: r["total"] for r in closed}

            open_rows = conn.execute("""
                SELECT desktop_id, started_at
                FROM usage_sessions
                WHERE session_date=? AND ended_at IS NULL
            """, (today,)).fetchall()
            for r in open_rows:
                elapsed = now - r["started_at"]
                result[r["desktop_id"]] = result.get(r["desktop_id"], 0) + elapsed
            return result

    def get_usage_history(self, days: int = 7) -> list[dict]:
        """Returns per-app per-day usage. days=0 means today only. Includes open sessions."""
        from datetime import timedelta
        today = date.today()
        today_str = today.isoformat()
        now = time.time()

        if days == 0:
            start_str = today_str
        else:
            start_str = (today - timedelta(days=days - 1)).isoformat()

        with self._connect() as conn:
            rows = conn.execute("""
                SELECT desktop_id, session_date,
                       SUM(duration_seconds) as total_seconds
                FROM usage_sessions
                WHERE session_date >= ? AND duration_seconds IS NOT NULL
                GROUP BY desktop_id, session_date
            """, (start_str,)).fetchall()

            result: dict[tuple, dict] = {}
            for r in rows:
                key = (r["desktop_id"], r["session_date"])
                result[key] = {"desktop_id": r["desktop_id"],
                               "session_date": r["session_date"],
                               "total_seconds": r["total_seconds"]}

            # Add currently-open sessions (they have no duration_seconds yet)
            open_rows = conn.execute("""
                SELECT desktop_id, started_at FROM usage_sessions
                WHERE session_date=? AND ended_at IS NULL
            """, (today_str,)).fetchall()
            for r in open_rows:
                elapsed = now - r["started_at"]
                key = (r["desktop_id"], today_str)
                if key in result:
                    result[key]["total_seconds"] += elapsed
                else:
                    result[key] = {"desktop_id": r["desktop_id"],
                                   "session_date": today_str,
                                   "total_seconds": elapsed}

        return sorted(result.values(),
                      key=lambda x: (x["session_date"], x["total_seconds"]),
                      reverse=True)

    def get_hourly_usage_today(self, desktop_id: str) -> dict[int, float]:
        """Returns {hour(0-23): seconds_used} for today, distributing sessions across hours."""
        from datetime import datetime as dt
        today_str = date.today().isoformat()
        today_midnight = dt.combine(date.today(), dt.min.time()).timestamp()
        now = time.time()

        with self._connect() as conn:
            rows = conn.execute("""
                SELECT started_at, ended_at FROM usage_sessions
                WHERE desktop_id=? AND session_date=?
            """, (desktop_id, today_str)).fetchall()

        hours = {h: 0.0 for h in range(24)}
        for row in rows:
            seg_start = row["started_at"]
            seg_end = row["ended_at"] if row["ended_at"] is not None else now
            for h in range(24):
                h_start = today_midnight + h * 3600
                h_end = h_start + 3600
                overlap = max(0.0, min(seg_end, h_end) - max(seg_start, h_start))
                if overlap > 0:
                    hours[h] += overlap
        return hours

    def get_daily_usage_for_app(self, desktop_id: str, days: int) -> dict[str, float]:
        """Returns {date_str: seconds} for an app over the past N days."""
        from datetime import timedelta
        today = date.today()
        now = time.time()

        date_range = [(today - timedelta(days=i)).isoformat() for i in range(days - 1, -1, -1)]
        start_str = date_range[0]

        with self._connect() as conn:
            rows = conn.execute("""
                SELECT session_date, SUM(duration_seconds) as total
                FROM usage_sessions
                WHERE desktop_id=? AND session_date >= ? AND duration_seconds IS NOT NULL
                GROUP BY session_date
            """, (desktop_id, start_str)).fetchall()

            result = {d: 0.0 for d in date_range}
            for r in rows:
                if r["session_date"] in result:
                    result[r["session_date"]] = r["total"] or 0.0

            # Add open sessions for today
            today_str = today.isoformat()
            open_rows = conn.execute("""
                SELECT started_at FROM usage_sessions
                WHERE desktop_id=? AND session_date=? AND ended_at IS NULL
            """, (desktop_id, today_str)).fetchall()
            for r in open_rows:
                result[today_str] = result.get(today_str, 0.0) + (now - r["started_at"])

        return result

    def get_setting(self, key: str, default: str = "") -> str:
        with self._connect() as conn:
            row = conn.execute(
                "SELECT value FROM settings WHERE key=?", (key,)
            ).fetchone()
            return row["value"] if row else default

    def set_setting(self, key: str, value: str):
        with self._connect() as conn:
            conn.execute(
                "INSERT OR REPLACE INTO settings (key, value) VALUES (?, ?)",
                (key, value)
            )

    def check_password(self, password: str) -> bool:
        stored = self.get_setting("admin_password_hash")
        return stored == _hash_password(password)

    def set_password(self, password: str):
        self.set_setting("admin_password_hash", _hash_password(password))


def _hash_password(password: str) -> str:
    return hashlib.pbkdf2_hmac(
        "sha256", password.encode(), b"screentime-salt", 100000
    ).hex()


def _row_to_app(row: sqlite3.Row) -> AppRecord:
    keys = row.keys()
    return AppRecord(
        id=row["id"],
        desktop_id=row["desktop_id"],
        name=row["name"],
        exec_binary=row["exec_binary"],
        icon=row["icon"],
        categories=row["categories"],
        allowed=bool(row["allowed"]),
        daily_limit_minutes=row["daily_limit_minutes"],
        exec_args=row["exec_args"] if "exec_args" in keys else "",
        limit_schedule=row["limit_schedule"] if "limit_schedule" in keys else "",
    )
