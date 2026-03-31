#!/usr/bin/env python3
"""Fullscreen overlay notification shown on the kid's screen.

Launched as the kid user by the root daemon (DaemonNotifier).
Completely standalone — no imports from the screentime package.

Usage:
    overlay.py --type blocked  --app "TikTok"
    overlay.py --type time_up  --app "Minecraft"
"""

import sys
import argparse

_COUNTDOWN_SECONDS = 6   # button enables after this, window auto-closes 2s later
_ICON_PATH_HINT = ""     # filled in by notifier if desired (not required)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--type", dest="ntype",
                        choices=["blocked", "time_up"], required=True)
    parser.add_argument("--app", required=True)
    args = parser.parse_args()

    from PyQt6.QtWidgets import (
        QApplication, QWidget, QVBoxLayout, QLabel, QPushButton,
        QGraphicsDropShadowEffect,
    )
    from PyQt6.QtCore import Qt, QTimer
    from PyQt6.QtGui import QFont, QColor, QIcon

    app = QApplication(sys.argv)
    app.setQuitOnLastWindowClosed(True)

    # Try to set app icon from theme
    icon = QIcon.fromTheme("screentime")
    if not icon.isNull():
        app.setWindowIcon(icon)

    if args.ntype == "blocked":
        card_color  = "#b71c1c"
        card_color2 = "#c62828"
        icon_text   = "🚫"
        title_text  = "Ứng dụng bị chặn"
        body_text   = f'"{args.app}"\nkhông được phép sử dụng.'
    else:  # time_up
        card_color  = "#e65100"
        card_color2 = "#f4511e"
        icon_text   = "⏰"
        title_text  = "Hết giờ!"
        body_text   = f'"{args.app}"\nđã hết thời gian sử dụng hôm nay.'

    # ── Main window ──────────────────────────────────────────────────────────
    window = QWidget()
    window.setWindowTitle("Screen Time")
    window.setWindowFlags(
        Qt.WindowType.FramelessWindowHint |
        Qt.WindowType.WindowStaysOnTopHint
    )
    window.setStyleSheet("QWidget { background: #0d0d1a; }")

    outer = QVBoxLayout(window)
    outer.setAlignment(Qt.AlignmentFlag.AlignCenter)

    # ── Card ─────────────────────────────────────────────────────────────────
    card = QWidget()
    card.setFixedSize(500, 350)
    card.setStyleSheet(f"""
        QWidget {{
            background: qlineargradient(
                x1:0, y1:0, x2:0, y2:1,
                stop:0 {card_color2}, stop:1 {card_color}
            );
            border-radius: 20px;
        }}
    """)

    shadow = QGraphicsDropShadowEffect()
    shadow.setBlurRadius(70)
    shadow.setColor(QColor(0, 0, 0, 200))
    shadow.setOffset(0, 10)
    card.setGraphicsEffect(shadow)

    card_layout = QVBoxLayout(card)
    card_layout.setContentsMargins(48, 36, 48, 32)
    card_layout.setSpacing(10)
    card_layout.setAlignment(Qt.AlignmentFlag.AlignCenter)

    # Icon emoji
    icon_lbl = QLabel(icon_text)
    f = QFont(); f.setPointSize(44)
    icon_lbl.setFont(f)
    icon_lbl.setAlignment(Qt.AlignmentFlag.AlignCenter)
    icon_lbl.setStyleSheet("color: white; background: transparent;")
    card_layout.addWidget(icon_lbl)

    # Title
    title_lbl = QLabel(title_text)
    f2 = QFont(); f2.setPointSize(22); f2.setBold(True)
    title_lbl.setFont(f2)
    title_lbl.setAlignment(Qt.AlignmentFlag.AlignCenter)
    title_lbl.setStyleSheet("color: white; background: transparent;")
    card_layout.addWidget(title_lbl)

    # Body
    body_lbl = QLabel(body_text)
    f3 = QFont(); f3.setPointSize(13)
    body_lbl.setFont(f3)
    body_lbl.setAlignment(Qt.AlignmentFlag.AlignCenter)
    body_lbl.setWordWrap(True)
    body_lbl.setStyleSheet("color: rgba(255,255,255,200); background: transparent;")
    card_layout.addWidget(body_lbl)

    card_layout.addSpacing(10)

    # Dismiss button (disabled during countdown)
    dismiss_btn = QPushButton(f"Đã hiểu  ({_COUNTDOWN_SECONDS})")
    dismiss_btn.setEnabled(False)
    dismiss_btn.setFixedHeight(44)
    dismiss_btn.setFixedWidth(200)
    dismiss_btn.setStyleSheet("""
        QPushButton {
            background: rgba(255,255,255,40);
            color: rgba(255,255,255,160);
            border-radius: 10px;
            font-size: 12pt;
            font-weight: bold;
            border: 2px solid rgba(255,255,255,80);
        }
        QPushButton:enabled {
            background: rgba(255,255,255,60);
            color: white;
            border: 2px solid rgba(255,255,255,160);
        }
        QPushButton:enabled:hover {
            background: rgba(255,255,255,90);
        }
        QPushButton:enabled:pressed {
            background: rgba(255,255,255,40);
        }
    """)
    dismiss_btn.clicked.connect(window.close)
    card_layout.addWidget(dismiss_btn, alignment=Qt.AlignmentFlag.AlignCenter)

    outer.addWidget(card)

    # ── Countdown logic ───────────────────────────────────────────────────────
    remaining = [_COUNTDOWN_SECONDS]

    def tick():
        remaining[0] -= 1
        if remaining[0] <= 0:
            dismiss_btn.setEnabled(True)
            dismiss_btn.setText("Đã hiểu")
            countdown.stop()
            QTimer.singleShot(2000, window.close)
        else:
            dismiss_btn.setText(f"Đã hiểu  ({remaining[0]})")

    countdown = QTimer()
    countdown.timeout.connect(tick)
    countdown.start(1000)

    # Block keyboard close during countdown
    original_key_press = window.keyPressEvent

    def _key_press(event):
        if not dismiss_btn.isEnabled():
            event.ignore()
            return
        original_key_press(event)

    window.keyPressEvent = _key_press

    window.showFullScreen()
    sys.exit(app.exec())


if __name__ == "__main__":
    main()
