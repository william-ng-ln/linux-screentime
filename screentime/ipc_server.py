"""IPC server — runs as a daemon thread inside the root daemon process.

Protocol: one JSON line per request, one JSON line per response, then close.
Socket: /run/screentime/control.sock, chmod 0666 (any user can connect).
Security is enforced at the protocol level:
  - Write commands require a valid session token.
  - Tokens are obtained by calling 'authenticate' with the admin password.
  - Tokens expire after TOKEN_TTL_SECONDS of inactivity (sliding window).
"""

import json
import logging
import os
import secrets
import socket
import threading
import time
from pathlib import Path

from .ipc import READ_COMMANDS, SOCKET_DIR, SOCKET_PATH, TOKEN_TTL_SECONDS, WRITE_COMMANDS
from .database import Database
from .desktop_scanner import scan_desktop_files

log = logging.getLogger(__name__)


class IpcServer(threading.Thread):
    def __init__(self, db: Database):
        super().__init__(name="ipc-server", daemon=True)
        self.db = db
        self._stop_event = threading.Event()
        self._server: socket.socket | None = None

        # Token store: {token: expires_at}  — in-memory only, never persisted
        self._tokens: dict[str, float] = {}
        self._tokens_lock = threading.Lock()

    def stop(self):
        self._stop_event.set()
        if self._server:
            try:
                self._server.close()
            except Exception:
                pass

    # ── Socket lifecycle ──────────────────────────────────────────────────────

    def run(self):
        SOCKET_DIR.mkdir(parents=True, exist_ok=True)

        try:
            SOCKET_PATH.unlink()
        except FileNotFoundError:
            pass

        server = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        self._server = server
        server.bind(str(SOCKET_PATH))
        os.chmod(SOCKET_PATH, 0o666)
        server.listen(8)
        server.settimeout(1.0)
        log.info("IPC server listening on %s", SOCKET_PATH)

        while not self._stop_event.is_set():
            try:
                conn, _ = server.accept()
                threading.Thread(
                    target=self._handle, args=(conn,), daemon=True
                ).start()
            except socket.timeout:
                continue
            except OSError:
                if not self._stop_event.is_set():
                    log.exception("IPC accept error")

        try:
            SOCKET_PATH.unlink()
        except FileNotFoundError:
            pass
        log.info("IPC server stopped")

    # ── Per-connection handler ────────────────────────────────────────────────

    def _handle(self, conn: socket.socket):
        try:
            rfile = conn.makefile("r", encoding="utf-8")
            wfile = conn.makefile("w", encoding="utf-8")

            line = rfile.readline()
            if not line.strip():
                return

            req = json.loads(line)
            cmd = req.get("cmd", "")
            args = req.get("args", {})

            try:
                if cmd in WRITE_COMMANDS:
                    if not self._valid_token(args.get("token", "")):
                        raise PermissionError("Chưa xác thực hoặc phiên đã hết hạn")
                elif cmd not in READ_COMMANDS:
                    raise ValueError(f"Lệnh không hợp lệ: {cmd!r}")

                data = self._dispatch(cmd, args)
                resp: dict = {"ok": True, "data": data}

            except PermissionError as e:
                resp = {"ok": False, "error": str(e), "unauthorized": True}
            except Exception as e:
                log.debug("IPC command %r failed: %s", cmd, e)
                resp = {"ok": False, "error": str(e)}

            json.dump(resp, wfile, separators=(",", ":"))
            wfile.write("\n")
            wfile.flush()

        except Exception as e:
            log.debug("IPC handler error: %s", e)
        finally:
            conn.close()

    # ── Token management ──────────────────────────────────────────────────────

    def _valid_token(self, token: str) -> bool:
        if not token:
            return False
        now = time.monotonic()
        with self._tokens_lock:
            expires = self._tokens.get(token)
            if expires is None or now > expires:
                self._tokens.pop(token, None)
                return False
            self._tokens[token] = now + TOKEN_TTL_SECONDS
            return True

    def _new_token(self) -> str:
        token = secrets.token_hex(32)
        with self._tokens_lock:
            self._tokens[token] = time.monotonic() + TOKEN_TTL_SECONDS
        return token

    def _revoke_all_tokens(self):
        with self._tokens_lock:
            self._tokens.clear()

    # ── Command dispatch ──────────────────────────────────────────────────────

    def _dispatch(self, cmd: str, args: dict):
        db = self.db

        # ── Auth ──────────────────────────────────────────────────────────────
        if cmd == "authenticate":
            if db.check_password(str(args.get("password", ""))):
                return self._new_token()
            raise ValueError("Sai mật khẩu")

        # ── Users ─────────────────────────────────────────────────────────────
        if cmd == "get_users":
            return [{"id": u.id, "username": u.username, "display_name": u.display_name}
                    for u in db.get_users()]

        if cmd == "add_user":
            uid = db.add_user(str(args["username"]), str(args.get("display_name", "")))
            return uid

        if cmd == "remove_user":
            db.remove_user(int(args["user_id"]))
            return None

        if cmd == "update_user":
            db.update_user(int(args["user_id"]), str(args.get("display_name", "")))
            return None

        # ── Read: apps ────────────────────────────────────────────────────────
        if cmd == "get_all_apps":
            user_id = int(args.get("user_id", 1))
            return [_app_dict(a) for a in db.get_all_apps(user_id)]

        if cmd == "get_app":
            user_id = int(args.get("user_id", 1))
            a = db.get_app(args["desktop_id"], user_id)
            return _app_dict(a) if a else None

        # ── Read: usage ───────────────────────────────────────────────────────
        if cmd == "get_today_usage":
            return db.get_today_usage(int(args.get("user_id", 1)))

        if cmd == "get_today_usage_including_open":
            return db.get_today_usage_including_open(int(args.get("user_id", 1)))

        if cmd == "get_usage_history":
            return db.get_usage_history(
                int(args.get("days", 7)),
                int(args.get("user_id", 1))
            )

        if cmd == "get_hourly_usage_today":
            return {str(k): v for k, v in db.get_hourly_usage_today(
                args["desktop_id"], int(args.get("user_id", 1))
            ).items()}

        if cmd == "get_daily_usage_for_app":
            return db.get_daily_usage_for_app(
                args["desktop_id"],
                int(args.get("days", 30)),
                int(args.get("user_id", 1))
            )

        # ── Read: settings ────────────────────────────────────────────────────
        if cmd == "get_setting":
            return db.get_setting(args["key"], args.get("default", ""))

        # ── Write: apps ───────────────────────────────────────────────────────
        if cmd == "set_app_allowed":
            db.set_app_allowed(
                args["desktop_id"],
                bool(args["allowed"]),
                int(args.get("user_id", 1))
            )
            return None

        if cmd == "set_app_schedule":
            db.set_app_schedule(
                args["desktop_id"],
                int(args["daily_limit_minutes"]),
                str(args["limit_schedule"]),
                int(args.get("user_id", 1))
            )
            return None

        # ── Write: settings ───────────────────────────────────────────────────
        if cmd == "set_setting":
            db.set_setting(str(args["key"]), str(args["value"]))
            return None

        if cmd == "set_password":
            db.set_password(str(args["new_password"]))
            self._revoke_all_tokens()
            return None

        # ── Write: scan ───────────────────────────────────────────────────────
        if cmd == "scan_apps":
            user_id = int(args.get("user_id", 1))
            users = db.get_users()
            user = next((u for u in users if u.id == user_id), None)
            username = user.username if user else ""
            return scan_desktop_files(db, user_id, username)

        raise ValueError(f"Lệnh không hợp lệ: {cmd!r}")


def _app_dict(a) -> dict:
    return {
        "desktop_id": a.desktop_id,
        "name": a.name,
        "exec_binary": a.exec_binary,
        "icon": a.icon,
        "categories": a.categories,
        "allowed": a.allowed,
        "daily_limit_minutes": a.daily_limit_minutes,
        "exec_args": a.exec_args,
        "limit_schedule": a.limit_schedule,
        "user_id": a.user_id,
        "id": a.id,
    }
