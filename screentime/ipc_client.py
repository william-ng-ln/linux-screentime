"""IPC client for the admin GUI.

Drop-in replacement for Database — identical public method signatures,
communicates with the root daemon over a Unix socket instead of
accessing the DB file directly.

Security model:
  - check_password() calls 'authenticate' on the daemon and stores the
    returned session token in memory.
  - All mutating calls include the token; the daemon validates it.
  - logout() clears the token (called when the admin window is locked).
"""

import json
import logging
import socket
from typing import Optional

from .ipc import SOCKET_PATH
from .database import AppRecord

log = logging.getLogger(__name__)

_ERR_CONNECT = (
    "Không thể kết nối đến daemon.\n"
    "Kiểm tra: sudo systemctl status screentime-daemon"
)
_ERR_SESSION = "Phiên đăng nhập hết hạn. Vui lòng đăng nhập lại."


class AdminClient:
    """Communicates with the screentime daemon over a Unix socket."""

    def __init__(self):
        self._token: str | None = None

    # ── Low-level call ────────────────────────────────────────────────────────

    def _call(self, cmd: str, **args) -> object:
        sock = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        try:
            sock.settimeout(5.0)
            sock.connect(str(SOCKET_PATH))
            rfile = sock.makefile("r", encoding="utf-8")
            wfile = sock.makefile("w", encoding="utf-8")

            json.dump({"cmd": cmd, "args": args}, wfile, separators=(",", ":"))
            wfile.write("\n")
            wfile.flush()

            line = rfile.readline()
            if not line:
                raise RuntimeError("Daemon đóng kết nối bất ngờ")
            resp = json.loads(line)
        except (ConnectionRefusedError, FileNotFoundError, OSError) as e:
            raise RuntimeError(_ERR_CONNECT) from e
        finally:
            try:
                sock.close()
            except Exception:
                pass

        if not resp.get("ok"):
            if resp.get("unauthorized"):
                self._token = None
                raise RuntimeError(_ERR_SESSION)
            raise RuntimeError(resp.get("error", "Lỗi IPC không xác định"))

        return resp.get("data")

    def _write(self, cmd: str, **args) -> object:
        """Call a write command — automatically injects the session token."""
        return self._call(cmd, token=self._token or "", **args)

    # ── Auth ──────────────────────────────────────────────────────────────────

    def check_password(self, password: str) -> bool:
        """Authenticate against the daemon. Stores the session token on success."""
        try:
            token = self._call("authenticate", password=password)
            if token:
                self._token = str(token)
                return True
            return False
        except RuntimeError as e:
            if "Sai mật khẩu" in str(e):
                return False
            raise   # re-raise connection errors so the UI can show them

    def logout(self):
        """Discard the session token (call when locking the admin window)."""
        self._token = None

    # ── Apps ──────────────────────────────────────────────────────────────────

    def get_all_apps(self) -> list[AppRecord]:
        return [_to_app(d) for d in (self._call("get_all_apps") or [])]

    def get_app(self, desktop_id: str) -> Optional[AppRecord]:
        data = self._call("get_app", desktop_id=desktop_id)
        return _to_app(data) if data else None

    def set_app_allowed(self, desktop_id: str, allowed: bool):
        self._write("set_app_allowed", desktop_id=desktop_id, allowed=allowed)

    def set_app_schedule(self, desktop_id: str,
                         daily_limit_minutes: int, limit_schedule: str):
        self._write("set_app_schedule",
                    desktop_id=desktop_id,
                    daily_limit_minutes=daily_limit_minutes,
                    limit_schedule=limit_schedule)

    # ── Usage ─────────────────────────────────────────────────────────────────

    def get_today_usage_including_open(self) -> dict[str, float]:
        return self._call("get_today_usage_including_open") or {}

    def get_usage_history(self, days: int = 7) -> list[dict]:
        return self._call("get_usage_history", days=days) or []

    def get_hourly_usage_today(self, desktop_id: str) -> dict[int, float]:
        raw = self._call("get_hourly_usage_today", desktop_id=desktop_id) or {}
        return {int(k): v for k, v in raw.items()}   # JSON keys are always strings

    def get_daily_usage_for_app(self, desktop_id: str,
                                days: int = 30) -> dict[str, float]:
        return self._call("get_daily_usage_for_app",
                          desktop_id=desktop_id, days=days) or {}

    # ── Settings ──────────────────────────────────────────────────────────────

    def get_setting(self, key: str, default: str = "") -> str:
        result = self._call("get_setting", key=key, default=default)
        return str(result) if result is not None else default

    def set_setting(self, key: str, value: str):
        self._write("set_setting", key=key, value=value)

    def set_password(self, new_password: str):
        self._write("set_password", new_password=new_password)

    # ── Scan ──────────────────────────────────────────────────────────────────

    def scan_apps(self) -> int:
        return int(self._write("scan_apps") or 0)

    # ── Compat stubs (daemon-only operations, not needed by the GUI) ──────────

    def initialize_schema(self):
        pass


def _to_app(d: dict) -> AppRecord:
    return AppRecord(
        desktop_id=d["desktop_id"],
        name=d["name"],
        exec_binary=d["exec_binary"],
        icon=d.get("icon", ""),
        categories=d.get("categories", ""),
        allowed=bool(d["allowed"]),
        daily_limit_minutes=int(d["daily_limit_minutes"]),
        id=d.get("id"),
        exec_args=d.get("exec_args", ""),
        limit_schedule=d.get("limit_schedule", ""),
    )
