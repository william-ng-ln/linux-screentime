#!/usr/bin/env python3
"""Screen Time — root daemon. Enforces screen time for all configured users."""

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

    # Scan desktop files for each configured user
    users = db.get_users()
    if not users:
        log.warning("No child users configured yet. Add users in the admin Settings.")
    for user in users:
        log.info("Scanning desktop files for user: %s", user.username)
        scan_desktop_files(db, user.id, user.username)

    tracker = TimeTracker(db)
    notifier = DaemonNotifier()
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
            for user in db.get_users():
                scan_desktop_files(db, user.id, user.username)
            enforcer.force_refresh()

    rescan_thread = threading.Thread(target=periodic_rescan, name="rescan", daemon=True)
    rescan_thread.start()

    log.info("Screen Time daemon running.")
    stop_event.wait()
    log.info("Screen Time daemon stopped.")


if __name__ == "__main__":
    main()
