import time
import logging
from threading import Lock
from datetime import date

from .database import Database

log = logging.getLogger(__name__)


class TimeTracker:
    """Tracks per-process usage in memory, flushes to DB when processes end."""

    def __init__(self, db: Database):
        self.db = db
        self._lock = Lock()
        # {pid: {'desktop_id': str, 'user_id': int, 'start': float, 'last_seen': float}}
        self._active: dict[int, dict] = {}

    def tick(self, pid: int, desktop_id: str, user_id: int):
        now = time.time()
        with self._lock:
            if pid not in self._active:
                self._active[pid] = {
                    "desktop_id": desktop_id,
                    "user_id": user_id,
                    "start": now,
                    "last_seen": now,
                }
                self.db.open_session(desktop_id, pid, now, user_id)
            else:
                self._active[pid]["last_seen"] = now

    def cleanup(self, alive_pids: set[int]):
        """Close sessions for PIDs that are no longer alive."""
        now = time.time()
        with self._lock:
            dead = set(self._active.keys()) - alive_pids
            for pid in dead:
                entry = self._active.pop(pid)
                last = entry["last_seen"]
                self.db.close_session(pid, last)
                log.debug("Closed session pid=%d app=%s", pid, entry["desktop_id"])

    def get_in_flight_seconds(self, user_id: int) -> dict[str, float]:
        """Returns seconds not yet flushed to DB for today's sessions for a user."""
        now = time.time()
        result: dict[str, float] = {}
        with self._lock:
            for entry in self._active.values():
                if entry["user_id"] == user_id:
                    elapsed = now - entry["start"]
                    desktop_id = entry["desktop_id"]
                    result[desktop_id] = result.get(desktop_id, 0) + elapsed
        return result

    def get_today_total(self, desktop_id: str, user_id: int) -> float:
        """Total seconds used today (DB + in-flight) for a specific user."""
        db_usage = self.db.get_today_usage(user_id)
        in_flight = self.get_in_flight_seconds(user_id)
        return db_usage.get(desktop_id, 0) + in_flight.get(desktop_id, 0)

    def flush_all(self):
        """Flush all in-flight sessions to DB (call on shutdown)."""
        now = time.time()
        with self._lock:
            for pid, entry in list(self._active.items()):
                self.db.close_session(pid, entry["last_seen"])
            self._active.clear()
