import os
import subprocess
import time
import logging
import threading
from datetime import date
from typing import Optional

import psutil

from .database import Database, AppRecord
from .time_tracker import TimeTracker
from . import config

log = logging.getLogger(__name__)

DB_REFRESH_INTERVAL = 30.0
WARN_MINUTES = 5


def _get_today_limit_minutes(app: AppRecord) -> int:
    if app.limit_schedule:
        parts = app.limit_schedule.split(",")
        if len(parts) == 7:
            try:
                return int(parts[date.today().weekday()])
            except (ValueError, IndexError):
                pass
    return app.daily_limit_minutes


class Enforcer(threading.Thread):
    def __init__(self, db: Database, tracker: TimeTracker, signals=None):
        super().__init__(name="enforcer", daemon=True)
        self.db = db
        self.tracker = tracker
        self.signals = signals
        self._stop_event = threading.Event()
        # Per-user app maps: {user_id: {exec_binary: AppRecord}}
        self._app_maps: dict[int, dict[str, AppRecord]] = {}
        self._cmdline_maps: dict[int, list[AppRecord]] = {}
        # {user_id: username}
        self._users: dict[int, str] = {}
        self._app_map_lock = threading.Lock()
        self._last_refresh = 0.0
        self._warned_date: str = ""
        # Per-user warned set: {user_id: set[desktop_id]}
        self._warned_ids: dict[int, set[str]] = {}

    def stop(self):
        self._stop_event.set()

    def _refresh_app_map(self):
        now = time.time()
        if now - self._last_refresh < DB_REFRESH_INTERVAL:
            return
        try:
            users = self.db.get_users()
            new_maps = self.db.get_all_users_exec_binaries()
            cmdline_maps = self.db.get_all_users_cmdline_apps()
            with self._app_map_lock:
                self._app_maps = new_maps
                self._cmdline_maps = cmdline_maps
                self._users = {u.id: u.username for u in users}
            self._last_refresh = now
        except Exception as e:
            log.error("Failed to refresh app map: %s", e)

    def force_refresh(self):
        self._last_refresh = 0.0
        self._refresh_app_map()

    def _get_app_for_process(self, proc: psutil.Process,
                             user_id: int) -> Optional[AppRecord]:
        with self._app_map_lock:
            app_map = dict(self._app_maps.get(user_id, {}))
            cmdline_apps = list(self._cmdline_maps.get(user_id, []))

        try:
            exe = proc.exe()
        except (psutil.NoSuchProcess, psutil.AccessDenied):
            return None

        exe_real = os.path.realpath(exe)

        real_map: dict[str, AppRecord] = {}
        for binary, app in app_map.items():
            real_map[os.path.realpath(binary)] = app

        # 1. Exact match
        if exe in app_map:
            return app_map[exe]
        if exe_real in real_map:
            return real_map[exe_real]

        # 2. Basename match
        exe_base = os.path.basename(exe_real)
        for real_bin, app in real_map.items():
            if os.path.basename(real_bin) == exe_base:
                return app

        # 3. Stem match in same directory
        exe_dir = os.path.dirname(exe_real)
        exe_stem = exe_base.split(".")[0]
        for real_bin, app in real_map.items():
            if (os.path.dirname(real_bin) == exe_dir and
                    os.path.basename(real_bin).split(".")[0] == exe_stem):
                return app

        # 4. Process name match
        try:
            proc_name = proc.name()
        except (psutil.NoSuchProcess, psutil.AccessDenied):
            return None
        for real_bin, app in real_map.items():
            if os.path.basename(real_bin) == proc_name:
                return app

        # 5. Cmdline match (waydroid and similar)
        if cmdline_apps:
            try:
                cmdline = " ".join(proc.cmdline())
            except (psutil.NoSuchProcess, psutil.AccessDenied):
                return None
            for app in cmdline_apps:
                if (app.exec_args and app.exec_args in cmdline and
                        exe_real == os.path.realpath(app.exec_binary)):
                    return app

        return None

    def _kill(self, proc: psutil.Process, app: Optional[AppRecord] = None):
        if (app and app.exec_args and
                os.path.basename(app.exec_binary) == "waydroid"):
            try:
                subprocess.run(["waydroid", "app", "stop", app.exec_args],
                               timeout=5, check=False)
                log.info("Stopped waydroid app: %s", app.exec_args)
            except Exception as e:
                log.debug("waydroid app stop failed: %s", e)

        try:
            children = proc.children(recursive=True)
        except (psutil.NoSuchProcess, psutil.AccessDenied):
            children = []

        for child in children:
            try:
                child.kill()
            except (psutil.NoSuchProcess, psutil.AccessDenied):
                pass

        try:
            proc.kill()
            log.info("Killed pid=%d name=%s", proc.pid, proc.name())
        except (psutil.NoSuchProcess, psutil.AccessDenied) as e:
            log.debug("Could not kill pid=%d: %s", proc.pid, e)

    def _notify_blocked(self, username: str, app_name: str):
        if self.signals:
            self.signals.app_blocked.emit(username, app_name)

    def _notify_warn(self, username: str, app_name: str,
                     minutes: int, app_icon: str = ""):
        if self.signals:
            self.signals.warn_approaching.emit(username, app_name, minutes, app_icon)

    def _notify_time_up(self, username: str, app_name: str):
        if self.signals:
            self.signals.time_up.emit(username, app_name)

    def _reset_warnings_if_new_day(self):
        today = date.today().isoformat()
        if today != self._warned_date:
            self._warned_date = today
            self._warned_ids = {}

    def run(self):
        log.info("Enforcer started (poll=%.1fs)", config.POLL_INTERVAL)
        self.force_refresh()

        while not self._stop_event.is_set():
            try:
                self._poll()
            except Exception as e:
                log.error("Enforcer poll error: %s", e)
            self._stop_event.wait(config.POLL_INTERVAL)

        log.info("Enforcer stopped")

    def _poll(self):
        enabled = self.db.get_setting("enabled", "0") == "1"
        self._refresh_app_map()
        self._reset_warnings_if_new_day()

        with self._app_map_lock:
            users = dict(self._users)   # {user_id: username}

        if not users:
            return  # no users configured yet

        username_to_id: dict[str, int] = {v: k for k, v in users.items()}
        monitored_usernames = set(users.values())

        alive_pids: set[int] = set()

        try:
            procs = list(psutil.process_iter(["pid", "username", "name", "exe"]))
        except Exception as e:
            log.error("psutil error: %s", e)
            return

        for proc in procs:
            try:
                username = proc.info.get("username") or proc.username()
                if username not in monitored_usernames:
                    continue

                user_id = username_to_id[username]

                name = proc.info.get("name") or proc.name()
                if name in config.SYSTEM_PROCESS_NAMES:
                    alive_pids.add(proc.pid)
                    continue

                app = self._get_app_for_process(proc, user_id)
                if app is None:
                    alive_pids.add(proc.pid)
                    continue

                alive_pids.add(proc.pid)

                if not enabled:
                    self.tracker.tick(proc.pid, app.desktop_id, user_id)
                    continue

                if not app.allowed:
                    self._notify_blocked(username, app.name)
                    self._kill(proc, app)
                    alive_pids.discard(proc.pid)
                    continue

                self.tracker.tick(proc.pid, app.desktop_id, user_id)

                limit_mins = _get_today_limit_minutes(app)
                if limit_mins > 0:
                    used = self.tracker.get_today_total(app.desktop_id, user_id)
                    limit_secs = limit_mins * 60
                    warn_secs = (limit_mins - WARN_MINUTES) * 60

                    if used >= limit_secs:
                        self._notify_time_up(username, app.name)
                        self._kill(proc, app)
                        alive_pids.discard(proc.pid)
                    elif (used >= warn_secs and
                          app.desktop_id not in self._warned_ids.get(user_id, set())):
                        remaining = max(1, int((limit_secs - used) / 60))
                        self._notify_warn(username, app.name, remaining, app.icon)
                        self._warned_ids.setdefault(user_id, set()).add(app.desktop_id)

            except (psutil.NoSuchProcess, psutil.AccessDenied):
                continue
            except Exception as e:
                log.debug("Process handling error: %s", e)

        self.tracker.cleanup(alive_pids)
