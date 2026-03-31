import subprocess
import logging
import pwd

import psutil

log = logging.getLogger(__name__)


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
        self.warn_approaching = _FakeSignal(
            lambda name, mins: _notify_as_user(
                username, "Sắp hết giờ",
                f'"{name}" còn {mins} phút hôm nay.',
                urgency="normal", icon="appointment-soon",
            )
        )
        self.time_up = _FakeSignal(
            lambda name: _notify_as_user(
                username, "Hết giờ!",
                f'"{name}" đã hết thời gian sử dụng hôm nay.',
                urgency="critical", icon="dialog-warning",
            )
        )
        self.app_blocked = _FakeSignal(
            lambda name: _notify_as_user(
                username, "Ứng dụng bị chặn",
                f'"{name}" không được phép sử dụng.',
                urgency="critical", icon="dialog-error",
            )
        )
