import sqlite3
import hashlib
import os
import time
from pathlib import Path
from dataclasses import dataclass, field
from typing import Optional
from datetime import date


@dataclass
class User:
    id: int
    username: str
    display_name: str = ""


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
    exec_args: str = ""
    limit_schedule: str = ""
    user_id: int = 1


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
        try:
            self.path.chmod(0o600)
            self.path.parent.chmod(0o755)
        except PermissionError:
            pass

    def initialize_schema(self):
        with self._connect() as conn:
            conn.executescript("""
                CREATE TABLE IF NOT EXISTS users (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    username TEXT UNIQUE NOT NULL,
                    display_name TEXT DEFAULT ''
                );

                CREATE TABLE IF NOT EXISTS applications (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    user_id INTEGER NOT NULL DEFAULT 1,
                    desktop_id TEXT NOT NULL,
                    name TEXT NOT NULL,
                    exec_binary TEXT NOT NULL,
                    icon TEXT DEFAULT '',
                    categories TEXT DEFAULT '',
                    allowed INTEGER DEFAULT 0,
                    daily_limit_minutes INTEGER DEFAULT 0,
                    exec_args TEXT DEFAULT '',
                    limit_schedule TEXT DEFAULT '',
                    UNIQUE(user_id, desktop_id)
                );

                CREATE TABLE IF NOT EXISTS usage_sessions (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    user_id INTEGER NOT NULL DEFAULT 1,
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
            defaults = {
                "admin_password_hash": _hash_password("admin"),
                "enabled": "0",
                "default_allow": "0",
                "kid_user": "",  # legacy; kept for migration
            }
            for k, v in defaults.items():
                conn.execute(
                    "INSERT OR IGNORE INTO settings (key, value) VALUES (?, ?)",
                    (k, v)
                )
        self._migrate()
        self._fix_permissions()

    def _migrate(self):
        """Upgrade existing databases to the multi-user schema."""
        # Step 1: ensure at least one user exists (migrate from legacy kid_user setting)
        with self._connect() as conn:
            count = conn.execute("SELECT COUNT(*) FROM users").fetchone()[0]
            if count == 0:
                row = conn.execute(
                    "SELECT value FROM settings WHERE key='kid_user'"
                ).fetchone()
                username = row["value"] if row and row["value"] else ""
                if username:
                    conn.execute(
                        "INSERT OR IGNORE INTO users (username, display_name) VALUES (?, ?)",
                        (username, username)
                    )

        # Step 2: migrate applications table if user_id column is missing
        conn = self._connect()
        try:
            app_cols = [r[1] for r in conn.execute("PRAGMA table_info(applications)").fetchall()]
            if "user_id" not in app_cols:
                first = conn.execute("SELECT id FROM users ORDER BY id LIMIT 1").fetchone()
                uid = first[0] if first else 1
                conn.executescript(f"""
                    ALTER TABLE applications RENAME TO _applications_v1;
                    CREATE TABLE applications (
                        id INTEGER PRIMARY KEY AUTOINCREMENT,
                        user_id INTEGER NOT NULL DEFAULT {uid},
                        desktop_id TEXT NOT NULL,
                        name TEXT NOT NULL,
                        exec_binary TEXT NOT NULL,
                        icon TEXT DEFAULT '',
                        categories TEXT DEFAULT '',
                        allowed INTEGER DEFAULT 0,
                        daily_limit_minutes INTEGER DEFAULT 0,
                        exec_args TEXT DEFAULT '',
                        limit_schedule TEXT DEFAULT '',
                        UNIQUE(user_id, desktop_id)
                    );
                    INSERT INTO applications
                        (user_id, desktop_id, name, exec_binary, icon, categories,
                         allowed, daily_limit_minutes, exec_args, limit_schedule)
                    SELECT {uid}, desktop_id, name, exec_binary, icon, categories,
                           allowed, daily_limit_minutes,
                           COALESCE(exec_args, ''), COALESCE(limit_schedule, '')
                    FROM _applications_v1;
                    DROP TABLE _applications_v1;
                """)
        finally:
            conn.close()

        # Step 3: add user_id to usage_sessions if missing
        conn = self._connect()
        try:
            sess_cols = [r[1] for r in conn.execute(
                "PRAGMA table_info(usage_sessions)"
            ).fetchall()]
            if "user_id" not in sess_cols:
                first = conn.execute("SELECT id FROM users ORDER BY id LIMIT 1").fetchone()
                uid = first[0] if first else 1
                conn.execute(
                    f"ALTER TABLE usage_sessions ADD COLUMN user_id INTEGER NOT NULL DEFAULT {uid}"
                )
                conn.commit()
        finally:
            conn.close()

    # ── User management ────────────────────────────────────────────────────────

    def get_users(self) -> list[User]:
        with self._connect() as conn:
            rows = conn.execute(
                "SELECT id, username, display_name FROM users ORDER BY id"
            ).fetchall()
        return [User(id=r["id"], username=r["username"], display_name=r["display_name"])
                for r in rows]

    def add_user(self, username: str, display_name: str = "") -> int:
        with self._connect() as conn:
            cur = conn.execute(
                "INSERT INTO users (username, display_name) VALUES (?, ?)",
                (username, display_name or username)
            )
            return cur.lastrowid

    def remove_user(self, user_id: int):
        with self._connect() as conn:
            conn.execute("DELETE FROM usage_sessions WHERE user_id=?", (user_id,))
            conn.execute("DELETE FROM applications WHERE user_id=?", (user_id,))
            conn.execute("DELETE FROM users WHERE id=?", (user_id,))

    def update_user(self, user_id: int, display_name: str):
        with self._connect() as conn:
            conn.execute(
                "UPDATE users SET display_name=? WHERE id=?",
                (display_name, user_id)
            )

    # ── Application management ─────────────────────────────────────────────────

    def upsert_application(self, app: AppRecord):
        with self._connect() as conn:
            default_allowed = self.get_setting("default_allow") == "1"
            conn.execute("""
                INSERT INTO applications
                    (user_id, desktop_id, name, exec_binary, icon, categories,
                     allowed, daily_limit_minutes, exec_args)
                VALUES (?, ?, ?, ?, ?, ?, ?, 0, ?)
                ON CONFLICT(user_id, desktop_id) DO UPDATE SET
                    name=excluded.name,
                    exec_binary=excluded.exec_binary,
                    icon=excluded.icon,
                    categories=excluded.categories,
                    exec_args=excluded.exec_args
            """, (
                app.user_id, app.desktop_id, app.name, app.exec_binary,
                app.icon, app.categories, 1 if default_allowed else 0,
                app.exec_args,
            ))

    def get_all_apps(self, user_id: int) -> list[AppRecord]:
        with self._connect() as conn:
            rows = conn.execute(
                "SELECT * FROM applications WHERE user_id=? ORDER BY name",
                (user_id,)
            ).fetchall()
        return [_row_to_app(r) for r in rows]

    def get_app(self, desktop_id: str, user_id: int) -> Optional[AppRecord]:
        with self._connect() as conn:
            row = conn.execute(
                "SELECT * FROM applications WHERE desktop_id=? AND user_id=?",
                (desktop_id, user_id)
            ).fetchone()
        return _row_to_app(row) if row else None

    def set_app_allowed(self, desktop_id: str, allowed: bool, user_id: int):
        with self._connect() as conn:
            conn.execute(
                "UPDATE applications SET allowed=? WHERE desktop_id=? AND user_id=?",
                (1 if allowed else 0, desktop_id, user_id)
            )

    def set_app_schedule(self, desktop_id: str, daily_limit_minutes: int,
                         limit_schedule: str, user_id: int):
        with self._connect() as conn:
            conn.execute(
                "UPDATE applications SET daily_limit_minutes=?, limit_schedule=?"
                " WHERE desktop_id=? AND user_id=?",
                (daily_limit_minutes, limit_schedule, desktop_id, user_id)
            )

    def get_exec_binaries(self, user_id: int) -> dict[str, AppRecord]:
        apps = self.get_all_apps(user_id)
        return {a.exec_binary: a for a in apps if a.exec_binary}

    def get_cmdline_apps(self, user_id: int) -> list[AppRecord]:
        apps = self.get_all_apps(user_id)
        return [a for a in apps if a.exec_args]

    def get_all_users_exec_binaries(self) -> dict[int, dict[str, AppRecord]]:
        """Returns {user_id: {exec_binary: AppRecord}} for all users."""
        with self._connect() as conn:
            rows = conn.execute("SELECT * FROM applications").fetchall()
        result: dict[int, dict[str, AppRecord]] = {}
        for row in rows:
            app = _row_to_app(row)
            if app.exec_binary:
                result.setdefault(app.user_id, {})[app.exec_binary] = app
        return result

    def get_all_users_cmdline_apps(self) -> dict[int, list[AppRecord]]:
        """Returns {user_id: [AppRecord]} for apps needing cmdline matching."""
        with self._connect() as conn:
            rows = conn.execute(
                "SELECT * FROM applications WHERE exec_args != ''"
            ).fetchall()
        result: dict[int, list[AppRecord]] = {}
        for row in rows:
            app = _row_to_app(row)
            result.setdefault(app.user_id, []).append(app)
        return result

    def remove_unlisted_apps(self, current_desktop_ids: set[str], user_id: int):
        if not current_desktop_ids:
            return
        placeholders = ",".join("?" * len(current_desktop_ids))
        with self._connect() as conn:
            conn.execute(
                f"DELETE FROM applications WHERE user_id=? AND desktop_id NOT IN ({placeholders})",
                [user_id, *current_desktop_ids]
            )

    # ── Sessions ───────────────────────────────────────────────────────────────

    def close_stale_sessions(self):
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

    def open_session(self, desktop_id: str, pid: int, started_at: float, user_id: int):
        with self._connect() as conn:
            conn.execute("""
                INSERT OR IGNORE INTO usage_sessions
                    (user_id, desktop_id, session_date, pid, started_at)
                VALUES (?, ?, ?, ?, ?)
            """, (user_id, desktop_id, date.today().isoformat(), pid, started_at))

    def close_session(self, pid: int, ended_at: float):
        with self._connect() as conn:
            row = conn.execute(
                "SELECT id, started_at FROM usage_sessions WHERE pid=? AND ended_at IS NULL",
                (pid,)
            ).fetchone()
            if row:
                duration = ended_at - row["started_at"]
                conn.execute("""
                    UPDATE usage_sessions SET ended_at=?, duration_seconds=? WHERE id=?
                """, (ended_at, duration, row["id"]))

    # ── Usage queries ──────────────────────────────────────────────────────────

    def get_today_usage(self, user_id: int) -> dict[str, float]:
        today = date.today().isoformat()
        with self._connect() as conn:
            rows = conn.execute("""
                SELECT desktop_id, SUM(duration_seconds) as total
                FROM usage_sessions
                WHERE user_id=? AND session_date=? AND duration_seconds IS NOT NULL
                GROUP BY desktop_id
            """, (user_id, today)).fetchall()
        return {r["desktop_id"]: r["total"] for r in rows}

    def get_today_usage_including_open(self, user_id: int) -> dict[str, float]:
        today = date.today().isoformat()
        now = time.time()
        with self._connect() as conn:
            closed = conn.execute("""
                SELECT desktop_id, SUM(duration_seconds) as total
                FROM usage_sessions
                WHERE user_id=? AND session_date=? AND duration_seconds IS NOT NULL
                GROUP BY desktop_id
            """, (user_id, today)).fetchall()
            result = {r["desktop_id"]: r["total"] for r in closed}

            open_rows = conn.execute("""
                SELECT desktop_id, started_at FROM usage_sessions
                WHERE user_id=? AND session_date=? AND ended_at IS NULL
            """, (user_id, today)).fetchall()
            for r in open_rows:
                elapsed = now - r["started_at"]
                result[r["desktop_id"]] = result.get(r["desktop_id"], 0) + elapsed
        return result

    def get_usage_history(self, days: int, user_id: int) -> list[dict]:
        from datetime import timedelta
        today = date.today()
        today_str = today.isoformat()
        now = time.time()
        start_str = today_str if days == 0 else (today - timedelta(days=days - 1)).isoformat()

        with self._connect() as conn:
            rows = conn.execute("""
                SELECT desktop_id, session_date, SUM(duration_seconds) as total_seconds
                FROM usage_sessions
                WHERE user_id=? AND session_date >= ? AND duration_seconds IS NOT NULL
                GROUP BY desktop_id, session_date
            """, (user_id, start_str)).fetchall()

            result: dict[tuple, dict] = {}
            for r in rows:
                key = (r["desktop_id"], r["session_date"])
                result[key] = {"desktop_id": r["desktop_id"],
                               "session_date": r["session_date"],
                               "total_seconds": r["total_seconds"]}

            open_rows = conn.execute("""
                SELECT desktop_id, started_at FROM usage_sessions
                WHERE user_id=? AND session_date=? AND ended_at IS NULL
            """, (user_id, today_str)).fetchall()
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

    def get_hourly_usage_today(self, desktop_id: str, user_id: int) -> dict[int, float]:
        from datetime import datetime as dt
        today_str = date.today().isoformat()
        today_midnight = dt.combine(date.today(), dt.min.time()).timestamp()
        now = time.time()

        with self._connect() as conn:
            rows = conn.execute("""
                SELECT started_at, ended_at FROM usage_sessions
                WHERE user_id=? AND desktop_id=? AND session_date=?
            """, (user_id, desktop_id, today_str)).fetchall()

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

    def get_daily_usage_for_app(self, desktop_id: str, days: int,
                                user_id: int) -> dict[str, float]:
        from datetime import timedelta
        today = date.today()
        now = time.time()
        date_range = [(today - timedelta(days=i)).isoformat() for i in range(days - 1, -1, -1)]
        start_str = date_range[0]

        with self._connect() as conn:
            rows = conn.execute("""
                SELECT session_date, SUM(duration_seconds) as total
                FROM usage_sessions
                WHERE user_id=? AND desktop_id=? AND session_date >= ?
                  AND duration_seconds IS NOT NULL
                GROUP BY session_date
            """, (user_id, desktop_id, start_str)).fetchall()

            result = {d: 0.0 for d in date_range}
            for r in rows:
                if r["session_date"] in result:
                    result[r["session_date"]] = r["total"] or 0.0

            today_str = today.isoformat()
            open_rows = conn.execute("""
                SELECT started_at FROM usage_sessions
                WHERE user_id=? AND desktop_id=? AND session_date=? AND ended_at IS NULL
            """, (user_id, desktop_id, today_str)).fetchall()
            for r in open_rows:
                result[today_str] = result.get(today_str, 0.0) + (now - r["started_at"])

        return result

    # ── Settings ───────────────────────────────────────────────────────────────

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
        user_id=row["user_id"] if "user_id" in keys else 1,
    )
