"""Qt signals for communicating from enforcer thread to main UI thread."""
from PyQt6.QtCore import QObject, pyqtSignal


class EnforcerSignals(QObject):
    # Emitted when an app has 5 minutes remaining (app_name, minutes_left)
    warn_approaching = pyqtSignal(str, int)
    # Emitted when an app has hit its time limit (app_name)
    time_up = pyqtSignal(str)
    # Emitted when a blocked (non-allowed) app is killed (app_name)
    app_blocked = pyqtSignal(str)
