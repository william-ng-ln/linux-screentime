"""System tray icon."""
from PyQt6.QtWidgets import QSystemTrayIcon, QMenu
from PyQt6.QtGui import QIcon, QPixmap, QPainter, QColor, QFont
from PyQt6.QtCore import QSize, Qt


def _app_icon() -> QIcon:
    """Load the screentime icon: installed theme icon → fallback drawn icon."""
    import os
    # 1. System-installed theme icon (set up by install.sh)
    icon = QIcon.fromTheme("screentime")
    if not icon.isNull():
        return icon

    # 2. SVG next to this package (development / uninstalled)
    pkg_root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    svg_path = os.path.join(pkg_root, "screentime.svg")
    if os.path.isfile(svg_path):
        icon = QIcon(svg_path)
        if not icon.isNull():
            return icon

    # 3. Drawn fallback
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
        self.setIcon(_app_icon())
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
