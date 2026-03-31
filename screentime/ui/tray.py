"""System tray icon."""
from PyQt6.QtWidgets import QSystemTrayIcon, QMenu
from PyQt6.QtGui import QIcon, QPixmap, QPainter, QColor, QFont
from PyQt6.QtCore import QSize, Qt


def _make_tray_icon() -> QIcon:
    """Create a simple clock-face icon for the tray."""
    # Try system theme icon first
    icon = QIcon.fromTheme("preferences-system-time")
    if not icon.isNull():
        return icon
    icon = QIcon.fromTheme("clock")
    if not icon.isNull():
        return icon

    # Fallback: draw a simple icon
    pix = QPixmap(32, 32)
    pix.fill(Qt.GlobalColor.transparent)
    painter = QPainter(pix)
    painter.setRenderHint(QPainter.RenderHint.Antialiasing)
    painter.setBrush(QColor("#1565c0"))
    painter.setPen(Qt.PenStyle.NoPen)
    painter.drawEllipse(2, 2, 28, 28)
    painter.setPen(QColor("white"))
    f = QFont(); f.setPointSize(12); f.setBold(True)
    painter.setFont(f)
    painter.drawText(pix.rect(), Qt.AlignmentFlag.AlignCenter, "ST")
    painter.end()
    return QIcon(pix)


class TrayIcon(QSystemTrayIcon):
    def __init__(self, on_open_admin, on_quit, parent=None):
        super().__init__(parent)
        self.setIcon(_make_tray_icon())
        self.setToolTip("Screen Time")

        menu = QMenu()
        open_action = menu.addAction("Mở quản lý")
        open_action.triggered.connect(on_open_admin)
        menu.addSeparator()
        quit_action = menu.addAction("Thoát Screen Time")
        quit_action.triggered.connect(on_quit)

        self.setContextMenu(menu)
        self.activated.connect(self._on_activated)
        self._on_open = on_open_admin

    def _on_activated(self, reason):
        if reason == QSystemTrayIcon.ActivationReason.Trigger:
            self._on_open()
