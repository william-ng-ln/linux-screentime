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
WARN_MINUTES = 5  # warn when this many minutes remain


def _get_today_limit_minutes(app: AppRecord) -> int:
    """Return today's effective time limit in minutes (0 = no limit)."""
    if app.limit_schedule:
        parts = app.limit_schedule.split(",")
        if len(parts) == 7:
            try:
                return int(parts[date.today().weekday()])  # 0=Mon, 6=Sun
            except (ValueError, IndexError):
                pass
    return app.daily_limit_minutes


class Enforcer(threading.Thread):
    def __init__(self, db: Database, tracker: TimeTracker, signals=None):
        super().__init__(name="enforcer", daemon=True)
        self.db = db
        self.tracker = tracker
        self.signals = signals  # EnforcerSignals (Qt), may be None
        self._stop_event = threading.Event()
        self._app_map: dict[str, AppRecord] = {}
        self._cmdline_apps: list[AppRecord] = []   # apps needing cmdline match
        self._app_map_lock = threading.Lock()
        self._last_refresh = 0.0
        # Track which apps have been warned today (reset at day change)
        self._warned_date: str = ""
        self._warned_ids: set[str] = set()

    def stop(self):
        self._stop_event.set()

    def _refresh_app_map(self):
        now = time.time()
        if now - self._last_refresh < DB_REFRESH_INTERVAL:
            return
        try:
            new_map = self.db.get_exec_binaries()
            cmdline_apps = self.db.get_cmdline_apps()
            with self._app_map_lock:
                self._app_map = new_map
                self._cmdline_apps = cmdline_apps
            self._last_refresh = now
        except Exception as e:
            log.error("Failed to refresh app map: %s", e)

    def force_refresh(self):
        self._last_refresh = 0.0
        self._refresh_app_map()

    def _get_app_for_process(self, proc: psutil.Process) -> Optional[AppRecord]:
        with self._app_map_lock:
            app_map = dict(self._app_map)

        try:
            exe = proc.exe()
        except (psutil.NoSuchProcess, psutil.AccessDenied):
            return None

        exe_real = os.path.realpath(exe)

        # Build a secondary map keyed by realpath of stored binaries (cached inline)
        # so symlink chains like /usr/bin/foo -> /opt/foo/foo are also matched.
        real_map: dict[str, AppRecord] = {}
        for binary, app in app_map.items():
            real_map[os.path.realpath(binary)] = app

        # 1. Exact match (original or realpath)
        if exe in app_map:
            return app_map[exe]
        if exe_real in real_map:
            return real_map[exe_real]

        # 2. Basename match
        exe_base = os.path.basename(exe_real)
        for real_bin, app in real_map.items():
            if os.path.basename(real_bin) == exe_base:
                return app

        # 3. Stem match in same directory: "soffice" matches "soffice.bin"
        #    Handles apps whose launcher script and real binary share a name prefix.
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

        # 5. Cmdline match for apps sharing a binary (e.g. waydroid apps)
        with self._app_map_lock:
            cmdline_apps = list(self._cmdline_apps)
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
        # For waydroid apps, also stop the Android process before killing the host process
        if (app and app.exec_args and
                os.path.basename(app.exec_binary) == "waydroid"):
            try:
                subprocess.run(["waydroid", "app", "stop", app.exec_args],
                               timeout=5, check=False)
                log.info("Stopped waydroid app: %s", app.exec_args)
            except Exception as e:
                log.debug("waydroid app stop failed: %s", e)

        # Kill the entire process tree so child processes (e.g. game launched by launcher) also die
        try:
            children = proc.children(recursive=True)
        except (psutil.NoSuchProcess, psutil.AccessDenied):
            children = []

        for child in children:
            try:
                child.kill()
                log.info("Killed child pid=%d name=%s", child.pid, child.name())
            except (psutil.NoSuchProcess, psutil.AccessDenied) as e:
                log.debug("Could not kill child pid=%d: %s", child.pid, e)

        try:
            proc.kill()
            log.info("Killed pid=%d name=%s", proc.pid, proc.name())
        except (psutil.NoSuchProcess, psutil.AccessDenied) as e:
            log.debug("Could not kill pid=%d: %s", proc.pid, e)

    def _notify_blocked(self, app_name: str):
        if self.signals:
            self.signals.app_blocked.emit(app_name)

    def _notify_warn(self, app_name: str, minutes: int):
        if self.signals:
            self.signals.warn_approaching.emit(app_name, minutes)

    def _notify_time_up(self, app_name: str):
        if self.signals:
            self.signals.time_up.emit(app_name)

    def _reset_warnings_if_new_day(self):
        today = date.today().isoformat()
        if today != self._warned_date:
            self._warned_date = today
            self._warned_ids = set()

    def run(self):
        log.info("Enforcer started (user=%s, poll=%.1fs)", config.TARGET_USER, config.POLL_INTERVAL)
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

        alive_pids: set[int] = set()

        try:
            procs = list(psutil.process_iter(["pid", "username", "name", "exe"]))
        except Exception as e:
            log.error("psutil error: %s", e)
            return

        for proc in procs:
            try:
                username = proc.info.get("username") or proc.username()
                if config.TARGET_USER and username != config.TARGET_USER:
                    continue

                name = proc.info.get("name") or proc.name()
                if name in config.SYSTEM_PROCESS_NAMES:
                    alive_pids.add(proc.pid)
                    continue

                app = self._get_app_for_process(proc)
                if app is None:
                    alive_pids.add(proc.pid)
                    continue

                alive_pids.add(proc.pid)

                if not enabled:
                    self.tracker.tick(proc.pid, app.desktop_id)
                    continue

                # Blocked by allowlist
                if not app.allowed:
                    self._notify_blocked(app.name)
                    self._kill(proc, app)
                    alive_pids.discard(proc.pid)
                    continue

                # Track time
                self.tracker.tick(proc.pid, app.desktop_id)

                # Check daily limit (schedule-aware)
                limit_mins = _get_today_limit_minutes(app)
                if limit_mins > 0:
                    used = self.tracker.get_today_total(app.desktop_id)
                    limit_secs = limit_mins * 60
                    warn_secs = (limit_mins - WARN_MINUTES) * 60

                    if used >= limit_secs:
                        # Time's up
                        self._notify_time_up(app.name)
                        self._kill(proc, app)
                        alive_pids.discard(proc.pid)
                    elif used >= warn_secs and app.desktop_id not in self._warned_ids:
                        # 5-minute warning (once per day)
                        remaining = max(1, int((limit_secs - used) / 60))
                        self._notify_warn(app.name, remaining)
                        self._warned_ids.add(app.desktop_id)

            except (psutil.NoSuchProcess, psutil.AccessDenied):
                continue
            except Exception as e:
                log.debug("Process handling error: %s", e)

        self.tracker.cleanup(alive_pids)
