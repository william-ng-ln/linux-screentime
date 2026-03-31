#!/usr/bin/env python3
"""Screen Time — root daemon. Enforces screen time for TARGET_USER."""

import os
import sys
import signal
import logging
import threading

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(name)s] %(levelname)s: %(message)s",
)
log = logging.getLogger("screentime.daemon")


def main():
    from screentime import config
    from screentime.database import Database
    from screentime.time_tracker import TimeTracker
    from screentime.enforcer import Enforcer
    from screentime.desktop_scanner import scan_desktop_files
    from screentime.notifier import DaemonNotifier
    from screentime.ipc_server import IpcServer

    db = Database(config.DB_PATH)
    db.initialize_schema()
    db.close_stale_sessions()
    log.info("Database: %s", config.DB_PATH)

    ipc = IpcServer(db)
    ipc.start()
    log.info("IPC server started")

    # Read kid username from DB; SCREENTIME_USER env var can override for testing.
    # Never use USER/LOGNAME — daemon runs as root so those would return "root".
    kid_user = os.environ.get("SCREENTIME_USER", "") or db.get_setting("kid_user", "")
    if not kid_user:
        log.warning("Kid username not configured yet. Set it in the admin Settings tab.")
        # Keep running so it's ready once configured; poll DB every 30s
        import time
        while not kid_user:
            time.sleep(30)
            kid_user = db.get_setting("kid_user", "")
            if kid_user:
                log.info("Kid username now configured: %s", kid_user)

    log.info("Monitoring user: %s", kid_user)

    log.info("Scanning desktop files...")
    os.environ["SCREENTIME_USER"] = kid_user
    # Reload config so TARGET_USER picks it up
    config.TARGET_USER = kid_user
    scan_desktop_files(db)

    tracker = TimeTracker(db)
    notifier = DaemonNotifier(kid_user)
    enforcer = Enforcer(db, tracker, signals=notifier)

    stop_event = threading.Event()
    rescan_stop = threading.Event()

    def shutdown(signum, frame):
        log.info("Shutting down (signal %d)...", signum)
        enforcer.stop()
        rescan_stop.set()
        tracker.flush_all()
        ipc.stop()
        stop_event.set()

    signal.signal(signal.SIGTERM, shutdown)
    signal.signal(signal.SIGINT, shutdown)

    enforcer.start()

    def periodic_rescan():
        while not rescan_stop.wait(config.DESKTOP_SCAN_INTERVAL):
            log.info("Periodic desktop rescan...")
            scan_desktop_files(db)

    rescan_thread = threading.Thread(target=periodic_rescan, name="rescan", daemon=True)
    rescan_thread.start()

    log.info("Screen Time daemon running.")
    stop_event.wait()
    log.info("Screen Time daemon stopped.")


if __name__ == "__main__":
    main()
