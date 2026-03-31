"""Warning and block dialogs shown to the child user."""
from PyQt6.QtWidgets import (
    QDialog, QLabel, QVBoxLayout, QPushButton, QHBoxLayout, QWidget
)
from PyQt6.QtCore import Qt, QTimer
from PyQt6.QtGui import QFont, QScreen


class WarningDialog(QDialog):
    """5-minute warning dialog — modal, auto-dismisses after 15s."""

    def __init__(self, app_name: str, minutes_left: int, parent=None):
        super().__init__(parent)
        self.setWindowTitle("Sắp hết thời gian")
        self.setWindowFlags(
            Qt.WindowType.Dialog |
            Qt.WindowType.WindowStaysOnTopHint |
            Qt.WindowType.WindowTitleHint |
            Qt.WindowType.CustomizeWindowHint
        )
        self.setFixedWidth(420)

        layout = QVBoxLayout(self)
        layout.setSpacing(16)
        layout.setContentsMargins(28, 24, 28, 20)

        # Icon + title row
        title = QLabel("⏰  Sắp hết thời gian!")
        title_font = QFont()
        title_font.setPointSize(14)
        title_font.setBold(True)
        title.setFont(title_font)
        title.setStyleSheet("color: #e65100;")
        layout.addWidget(title)

        msg = QLabel(
            f'<b>{app_name}</b> còn <b>{minutes_left} phút</b> sử dụng hôm nay.<br>'
            f'Hãy lưu công việc lại và chuẩn bị đóng ứng dụng.'
        )
        msg.setWordWrap(True)
        msg.setStyleSheet("font-size: 11pt; color: #333;")
        layout.addWidget(msg)

        self._countdown = 15
        self._countdown_label = QLabel(f"Tự đóng sau {self._countdown}s")
        self._countdown_label.setStyleSheet("color: #888; font-size: 9pt;")
        layout.addWidget(self._countdown_label)

        btn = QPushButton("Đã hiểu")
        btn.setFixedHeight(36)
        btn.setStyleSheet(
            "QPushButton { background: #e65100; color: white; border-radius: 6px; font-weight: bold; }"
            "QPushButton:hover { background: #bf360c; }"
        )
        btn.clicked.connect(self.accept)
        layout.addWidget(btn)

        self._timer = QTimer(self)
        self._timer.timeout.connect(self._tick)
        self._timer.start(1000)

        self._center()

    def _tick(self):
        self._countdown -= 1
        self._countdown_label.setText(f"Tự đóng sau {self._countdown}s")
        if self._countdown <= 0:
            self._timer.stop()
            self.accept()

    def _center(self):
        screen = QScreen.availableVirtualGeometry(self.screen())
        geo = self.frameGeometry()
        geo.moveCenter(screen.center())
        self.move(geo.topLeft())


class TimeUpDialog(QDialog):
    """Fullscreen dialog shown when time is up — cannot be dismissed, auto-closes."""

    def __init__(self, app_name: str, parent=None):
        super().__init__(parent)
        self.setWindowTitle("Hết thời gian")
        self.setWindowFlags(
            Qt.WindowType.FramelessWindowHint |
            Qt.WindowType.WindowStaysOnTopHint |
            Qt.WindowType.Tool
        )
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground, False)
        self.setStyleSheet("background-color: #1a1a2e;")

        layout = QVBoxLayout(self)
        layout.setAlignment(Qt.AlignmentFlag.AlignCenter)
        layout.setSpacing(24)

        icon = QLabel("⏱")
        icon_font = QFont()
        icon_font.setPointSize(72)
        icon.setFont(icon_font)
        icon.setAlignment(Qt.AlignmentFlag.AlignCenter)
        layout.addWidget(icon)

        title = QLabel("Hết thời gian sử dụng")
        t_font = QFont()
        t_font.setPointSize(32)
        t_font.setBold(True)
        title.setFont(t_font)
        title.setAlignment(Qt.AlignmentFlag.AlignCenter)
        title.setStyleSheet("color: #e94560;")
        layout.addWidget(title)

        subtitle = QLabel(f'<b style="color:#fff">{app_name}</b>'
                          '<span style="color:#aaa"> đã dùng hết thời gian cho hôm nay.</span>')
        s_font = QFont()
        s_font.setPointSize(16)
        subtitle.setFont(s_font)
        subtitle.setAlignment(Qt.AlignmentFlag.AlignCenter)
        subtitle.setStyleSheet("color: #ccc;")
        layout.addWidget(subtitle)

        self._countdown = 8
        self._label = QLabel(f"Ứng dụng sẽ đóng sau {self._countdown} giây...")
        c_font = QFont()
        c_font.setPointSize(13)
        self._label.setFont(c_font)
        self._label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self._label.setStyleSheet("color: #888;")
        layout.addWidget(self._label)

        self._timer = QTimer(self)
        self._timer.timeout.connect(self._tick)
        self._timer.start(1000)

        self.showFullScreen()

    def _tick(self):
        self._countdown -= 1
        self._label.setText(f"Ứng dụng sẽ đóng sau {self._countdown} giây...")
        if self._countdown <= 0:
            self._timer.stop()
            self.accept()

    # Prevent closing via Escape or Alt+F4
    def keyPressEvent(self, event):
        pass

    def closeEvent(self, event):
        if self._countdown > 0:
            event.ignore()
        else:
            event.accept()
