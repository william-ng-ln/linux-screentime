#!/usr/bin/env python3
"""Screen Time — Qt admin GUI for parents. Reads the shared DB written by the root daemon."""

import os
import sys
import signal
import logging

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(name)s] %(levelname)s: %(message)s",
)
log = logging.getLogger("screentime")

PID_FILE = os.path.join(
    os.environ.get("XDG_RUNTIME_DIR", f"/tmp"),
    "screentime-admin.pid"
)


def _try_show_existing() -> bool:
    """If another instance is running, signal it to show the window and return True."""
    try:
        with open(PID_FILE) as f:
            pid = int(f.read().strip())
        # Check the process is actually alive
        os.kill(pid, 0)
        # Send SIGUSR1 to tell it to raise the admin window
        os.kill(pid, signal.SIGUSR1)
        log.info("Sent SIGUSR1 to existing instance (pid=%d)", pid)
        return True
    except (FileNotFoundError, ValueError, ProcessLookupError, PermissionError):
        return False


def _write_pid():
    with open(PID_FILE, "w") as f:
        f.write(str(os.getpid()))


def _remove_pid():
    try:
        os.unlink(PID_FILE)
    except FileNotFoundError:
        pass


def _load_app_icon():
    """Load the screentime icon: installed theme → SVG file → drawn fallback."""
    import os
    from PyQt6.QtGui import QIcon, QPixmap, QPainter, QColor, QBrush, QPen
    from PyQt6.QtCore import Qt

    # 1. System-installed theme icon (set up by install.sh)
    icon = QIcon.fromTheme("screentime")
    if not icon.isNull():
        return icon

    # 2. SVG next to main.py (development)
    svg_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), "screentime.svg")
    if os.path.isfile(svg_path):
        icon = QIcon(svg_path)
        if not icon.isNull():
            return icon

    # 3. Drawn fallback
    pix = QPixmap(64, 64)
    pix.fill(Qt.GlobalColor.transparent)
    p = QPainter(pix)
    p.setRenderHint(QPainter.RenderHint.Antialiasing)
    p.setPen(Qt.PenStyle.NoPen)
    p.setBrush(QBrush(QColor("#1565c0")))
    p.drawEllipse(2, 2, 60, 60)
    p.setBrush(QBrush(QColor("white")))
    p.drawEllipse(8, 8, 48, 48)
    pen = QPen(QColor("#1565c0"), 5)
    pen.setCapStyle(Qt.PenCapStyle.RoundCap)
    p.setPen(pen)
    p.drawLine(32, 32, 22, 20)
    p.drawLine(32, 32, 43, 19)
    p.setPen(Qt.PenStyle.NoPen)
    p.setBrush(QBrush(QColor("#0d47a1")))
    p.drawEllipse(29, 29, 6, 6)
    p.end()
    return QIcon(pix)


def main():
    if _try_show_existing():
        sys.exit(0)

    _write_pid()

    from PyQt6.QtWidgets import QApplication
    from PyQt6.QtCore import QTimer

    from screentime import config
    from screentime.database import Database
    from screentime.ui.tray import TrayIcon
    from screentime.ui.admin_window import AdminWindow

    app = QApplication(sys.argv)
    app.setApplicationName("Screen Time")
    app.setQuitOnLastWindowClosed(False)

    icon = _load_app_icon()
    app.setWindowIcon(icon)

    db = Database(config.DB_PATH)
    db.initialize_schema()
    log.info("Database: %s", config.DB_PATH)

    admin_win = AdminWindow(db)

    tray = TrayIcon(
        on_open_admin=admin_win.open_and_raise,
        on_quit=app.quit,
    )
    tray.show()

    # SIGUSR1: show admin window (sent by a second instance launched from start menu)
    def _on_sigusr1(*_):
        admin_win.open_and_raise()

    # Qt requires signals to be handled via a timer trick on the main thread
    signal.signal(signal.SIGUSR1, _on_sigusr1)
    # Wake Qt event loop every 500ms so Python signal handlers can run
    _sig_timer = QTimer()
    _sig_timer.start(500)
    _sig_timer.timeout.connect(lambda: None)

    signal.signal(signal.SIGINT, lambda *_: app.quit())
    signal.signal(signal.SIGTERM, lambda *_: app.quit())

    def _on_quit():
        _remove_pid()

    app.aboutToQuit.connect(_on_quit)

    log.info("Screen Time admin UI running. Open from system tray.")
    sys.exit(app.exec())


if __name__ == "__main__":
    main()
