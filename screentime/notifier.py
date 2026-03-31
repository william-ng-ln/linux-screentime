import os
import subprocess
import logging
import time
import pwd

import psutil

log = logging.getLogger(__name__)

_PKG_DIR = os.path.dirname(os.path.abspath(__file__))   # .../screentime/
_ROOT_DIR = os.path.dirname(_PKG_DIR)                   # .../opt/screentime/
_VENV_PYTHON = os.path.join(_ROOT_DIR, ".venv", "bin", "python3")
_OVERLAY_SCRIPT = os.path.join(_PKG_DIR, "overlay.py")

# Cooldown: don't show another overlay for the same app within this many seconds
_OVERLAY_COOLDOWN = 30.0


def _get_user_session_env(username: str) -> dict:
    """Find DISPLAY/WAYLAND_DISPLAY/DBUS env vars from a user's running processes."""
    try:
        for proc in psutil.process_iter(["username", "pid"]):
            try:
                if proc.info.get("username") != username:
                    continue
                env_path = f"/proc/{proc.pid}/environ"
                with open(env_path, "rb") as f:
                    raw = f.read()
                env = {}
                for item in raw.split(b"\x00"):
                    if b"=" in item:
                        k, v = item.split(b"=", 1)
                        env[k.decode("utf-8", errors="replace")] = v.decode("utf-8", errors="replace")
                if "DISPLAY" in env or "WAYLAND_DISPLAY" in env:
                    return env
            except (psutil.NoSuchProcess, psutil.AccessDenied, OSError):
                continue
    except Exception as e:
        log.debug("get_user_session_env error: %s", e)
    return {}


def _show_overlay_as_user(username: str, ntype: str, app_name: str):
    """Launch the fullscreen overlay on the kid's desktop session (call from root)."""
    env = _get_user_session_env(username)
    if not env:
        log.warning("Could not find session env for user %s, skipping overlay", username)
        return

    display_env = {
        k: env[k]
        for k in ["DISPLAY", "WAYLAND_DISPLAY", "DBUS_SESSION_BUS_ADDRESS",
                  "XDG_RUNTIME_DIR", "WAYLAND_SOCKET"]
        if k in env
    }
    if not display_env:
        return

    python = _VENV_PYTHON if os.path.isfile(_VENV_PYTHON) else "python3"

    try:
        pw = pwd.getpwnam(username)
        full_env = {
            **display_env,
            "HOME": pw.pw_dir,
            "USER": username,
            "LOGNAME": username,
            "PATH": "/usr/local/bin:/usr/bin:/bin",
        }
        subprocess.Popen(
            [python, _OVERLAY_SCRIPT, "--type", ntype, "--app", app_name],
            env=full_env,
            user=pw.pw_uid,
        )
        log.info("Overlay launched: type=%s app=%r user=%s", ntype, app_name, username)
    except Exception as e:
        log.warning("Overlay launch failed (%s), falling back to notify-send: %s", ntype, e)
        _notify_as_user(username, "Hết giờ!" if ntype == "time_up" else "Ứng dụng bị chặn",
                        app_name, urgency="critical", icon="dialog-warning")


def _resolve_notify_icon(icon: str, fallback: str = "appointment-soon") -> str:
    """Return the best icon identifier to pass to notify-send.

    - Absolute path that exists on disk → return as-is
    - Non-empty theme name (no path separators) → return as-is (notification daemon resolves it)
    - Anything else / empty → return fallback
    """
    if not icon:
        return fallback
    if os.path.isabs(icon):
        return icon if os.path.isfile(icon) else fallback
    # Theme name (e.g. "firefox", "org.mozilla.firefox")
    if "/" not in icon:
        return icon
    return fallback


def _notify_as_user(username: str, summary: str, body: str,
                    urgency: str = "normal", icon: str = "dialog-information"):
    """Run notify-send in the user's desktop session (call from root)."""
    env = _get_user_session_env(username)
    if not env:
        log.warning("Could not find session env for user %s, skipping notification", username)
        return

    display_env = {
        k: env[k]
        for k in ["DISPLAY", "WAYLAND_DISPLAY", "DBUS_SESSION_BUS_ADDRESS", "XDG_RUNTIME_DIR"]
        if k in env
    }
    if not display_env:
        return

    try:
        pw = pwd.getpwnam(username)
        full_env = {**display_env, "HOME": pw.pw_dir, "USER": username, "LOGNAME": username}
        subprocess.Popen(
            ["notify-send", f"--urgency={urgency}", f"--icon={icon}", "-t", "10000", summary, body],
            env=full_env,
            user=pw.pw_uid,
        )
    except FileNotFoundError:
        log.warning("notify-send not found; skipping notification")
    except Exception as e:
        log.warning("Notification failed: %s", e)


class _FakeSignal:
    """Mimics PyQt signal's .emit() interface for non-Qt contexts."""
    def __init__(self, fn):
        self._fn = fn

    def emit(self, *args):
        self._fn(*args)


class DaemonNotifier:
    """Duck-typed replacement for EnforcerSignals for use in the root daemon (no Qt)."""

    def __init__(self, username: str):
        self._username = username
        # Cooldown: {app_name: last_notify_timestamp} — prevents overlay spam
        self._last_overlay: dict[str, float] = {}

        self.warn_approaching = _FakeSignal(self._on_warn)
        self.time_up = _FakeSignal(self._on_time_up)
        self.app_blocked = _FakeSignal(self._on_blocked)

    def _cooldown_ok(self, app_name: str) -> bool:
        """Return True if enough time has passed since the last overlay for this app."""
        now = time.monotonic()
        last = self._last_overlay.get(app_name, 0.0)
        if now - last >= _OVERLAY_COOLDOWN:
            self._last_overlay[app_name] = now
            return True
        return False

    def _on_warn(self, name: str, mins: int, app_icon: str = ""):
        _notify_as_user(
            self._username,
            "Sắp hết giờ",
            f'"{name}" còn {mins} phút hôm nay.',
            urgency="normal",
            icon=_resolve_notify_icon(app_icon),
        )

    def _on_time_up(self, name: str):
        if self._cooldown_ok(name):
            _show_overlay_as_user(self._username, "time_up", name)

    def _on_blocked(self, name: str):
        if self._cooldown_ok(name):
            _show_overlay_as_user(self._username, "blocked", name)
