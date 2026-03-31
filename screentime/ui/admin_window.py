"""Password-protected admin window for managing screen time."""
from PyQt6.QtWidgets import (
    QMainWindow, QWidget, QVBoxLayout, QHBoxLayout, QTabWidget,
    QLabel, QPushButton, QLineEdit, QTableWidget, QTableWidgetItem,
    QCheckBox, QSpinBox, QHeaderView, QMessageBox, QStackedWidget,
    QFormLayout, QGroupBox, QAbstractItemView, QSplitter, QFrame,
    QSizePolicy, QDialog, QRadioButton, QButtonGroup
)
from PyQt6.QtCore import Qt, QTimer, QSize, QRect
from PyQt6.QtGui import QFont, QIcon, QColor, QPainter, QPen, QBrush

from datetime import date

from ..database import Database, AppRecord
from ..desktop_scanner import scan_desktop_files

_DAYS_VN = ["T2", "T3", "T4", "T5", "T6", "T7", "CN"]
_DAYS_FULL = ["Thứ Hai", "Thứ Ba", "Thứ Tư", "Thứ Năm", "Thứ Sáu", "Thứ Bảy", "Chủ Nhật"]


def _effective_limit(app: AppRecord) -> int:
    """Return today's effective time limit in minutes (mirrors enforcer logic)."""
    if app.limit_schedule:
        parts = app.limit_schedule.split(",")
        if len(parts) == 7:
            try:
                return int(parts[date.today().weekday()])
            except (ValueError, IndexError):
                pass
    return app.daily_limit_minutes


def _schedule_label(app: AppRecord) -> str:
    """Human-readable summary of an app's time limit for display in the table."""
    if app.limit_schedule:
        parts = app.limit_schedule.split(",")
        if len(parts) == 7:
            try:
                vals = [int(p) for p in parts]
                if len(set(vals)) == 1:
                    return f"{vals[0]}p/ngày" if vals[0] > 0 else "Không giới hạn"
                wday = vals[0]  # Mon value as representative
                wend = vals[5]  # Sat value as representative
                return f"T2-T6: {wday}p, T7-CN: {wend}p" if wday != wend else "Lịch tuần"
            except ValueError:
                pass
    if app.daily_limit_minutes > 0:
        return f"{app.daily_limit_minutes}p/ngày"
    return "Không giới hạn"


class ScheduleDialog(QDialog):
    """Dialog for setting per-app time limits (uniform daily or per-day-of-week)."""

    def __init__(self, app_name: str, daily_limit_minutes: int, limit_schedule: str, parent=None):
        super().__init__(parent)
        self.setWindowTitle(f"Giới hạn thời gian: {app_name}")
        self.setFixedWidth(400)
        self._build(daily_limit_minutes, limit_schedule)

    def _build(self, daily_limit: int, schedule: str):
        layout = QVBoxLayout(self)
        layout.setSpacing(12)
        layout.setContentsMargins(16, 16, 16, 16)

        # Parse existing schedule
        sched_vals = [0] * 7
        has_schedule = False
        if schedule:
            parts = schedule.split(",")
            if len(parts) == 7:
                try:
                    sched_vals = [int(p) for p in parts]
                    has_schedule = True
                except ValueError:
                    pass

        # Radio: No limit
        self._radio_unlimited = QRadioButton("Không giới hạn")

        # Radio: Same every day
        daily_row = QHBoxLayout()
        self._radio_daily = QRadioButton("Mỗi ngày:")
        daily_row.addWidget(self._radio_daily)
        self._daily_spin = QSpinBox()
        self._daily_spin.setRange(1, 1440)
        self._daily_spin.setValue(daily_limit if daily_limit > 0 else 60)
        self._daily_spin.setSuffix(" phút")
        daily_row.addWidget(self._daily_spin)
        daily_row.addStretch()

        # Radio: Per day of week
        self._radio_schedule = QRadioButton("Theo ngày trong tuần:")

        self._btn_group = QButtonGroup(self)
        self._btn_group.addButton(self._radio_unlimited, 0)
        self._btn_group.addButton(self._radio_daily, 1)
        self._btn_group.addButton(self._radio_schedule, 2)

        layout.addWidget(self._radio_unlimited)
        layout.addLayout(daily_row)
        layout.addWidget(self._radio_schedule)

        # Per-day spinboxes
        self._day_spins: list[QSpinBox] = []
        grid = QFormLayout()
        grid.setContentsMargins(20, 0, 0, 0)
        for i, (short, full) in enumerate(zip(_DAYS_VN, _DAYS_FULL)):
            spin = QSpinBox()
            spin.setRange(0, 1440)
            spin.setValue(sched_vals[i])
            spin.setSuffix(" phút")
            spin.setSpecialValueText("Không giới hạn")
            grid.addRow(f"{short} – {full}:", spin)
            self._day_spins.append(spin)
        layout.addLayout(grid)

        # Set initial mode
        if has_schedule:
            self._radio_schedule.setChecked(True)
        elif daily_limit > 0:
            self._radio_daily.setChecked(True)
        else:
            self._radio_unlimited.setChecked(True)

        self._radio_unlimited.toggled.connect(self._on_mode_changed)
        self._radio_daily.toggled.connect(self._on_mode_changed)
        self._radio_schedule.toggled.connect(self._on_mode_changed)
        self._on_mode_changed()

        # OK / Cancel
        btns = QHBoxLayout()
        ok_btn = QPushButton("Lưu")
        ok_btn.setStyleSheet(
            "QPushButton { background: #1565c0; color: white; border-radius: 4px; padding: 6px 20px; font-weight: bold; }"
            "QPushButton:hover { background: #0d47a1; }"
        )
        ok_btn.clicked.connect(self.accept)
        cancel_btn = QPushButton("Huỷ")
        cancel_btn.clicked.connect(self.reject)
        btns.addStretch()
        btns.addWidget(cancel_btn)
        btns.addWidget(ok_btn)
        layout.addLayout(btns)

    def _on_mode_changed(self):
        mode = self._btn_group.checkedId()
        self._daily_spin.setEnabled(mode == 1)
        for spin in self._day_spins:
            spin.setEnabled(mode == 2)

    def get_result(self) -> tuple[int, str]:
        """Returns (daily_limit_minutes, limit_schedule)."""
        mode = self._btn_group.checkedId()
        if mode == 0:
            return 0, ""
        elif mode == 1:
            return self._daily_spin.value(), ""
        else:
            return 0, ",".join(str(s.value()) for s in self._day_spins)


class LoginWidget(QWidget):
    def __init__(self, db: Database, on_success, parent=None):
        super().__init__(parent)
        self.db = db
        self.on_success = on_success
        self._build()

    def _build(self):
        outer = QVBoxLayout(self)
        outer.setAlignment(Qt.AlignmentFlag.AlignCenter)

        card = QWidget()
        card.setFixedWidth(340)
        card.setStyleSheet(
            "QWidget { background: white; border-radius: 12px; }"
        )
        layout = QVBoxLayout(card)
        layout.setContentsMargins(32, 28, 32, 28)
        layout.setSpacing(14)

        title = QLabel("Screen Time")
        f = QFont(); f.setPointSize(18); f.setBold(True)
        title.setFont(f)
        title.setStyleSheet("color: #1565c0;")
        title.setAlignment(Qt.AlignmentFlag.AlignCenter)
        layout.addWidget(title)

        sub = QLabel("Trang quản lý dành cho phụ huynh")
        sub.setAlignment(Qt.AlignmentFlag.AlignCenter)
        sub.setStyleSheet("color: #888; font-size: 10pt;")
        layout.addWidget(sub)

        layout.addSpacing(8)

        lbl = QLabel("Mật khẩu quản trị")
        lbl.setStyleSheet("font-weight: bold; font-size: 10pt;")
        layout.addWidget(lbl)

        self._pwd = QLineEdit()
        self._pwd.setEchoMode(QLineEdit.EchoMode.Password)
        self._pwd.setPlaceholderText("Nhập mật khẩu...")
        self._pwd.setFixedHeight(38)
        self._pwd.returnPressed.connect(self._login)
        layout.addWidget(self._pwd)

        self._error = QLabel("")
        self._error.setStyleSheet("color: #c62828; font-size: 9pt;")
        self._error.setAlignment(Qt.AlignmentFlag.AlignCenter)
        layout.addWidget(self._error)

        btn = QPushButton("Đăng nhập")
        btn.setFixedHeight(38)
        btn.setStyleSheet(
            "QPushButton { background: #1565c0; color: white; border-radius: 6px; font-weight: bold; font-size: 11pt; }"
            "QPushButton:hover { background: #0d47a1; }"
        )
        btn.clicked.connect(self._login)
        layout.addWidget(btn)

        outer.addWidget(card)

    def _login(self):
        if self.db.check_password(self._pwd.text()):
            self._pwd.clear()
            self._error.setText("")
            self.on_success()
        else:
            self._error.setText("Sai mật khẩu")
            self._pwd.clear()
            self._pwd.setFocus()

    def showEvent(self, event):
        super().showEvent(event)
        self._pwd.setFocus()


class AppsTab(QWidget):
    def __init__(self, db: Database, parent=None):
        super().__init__(parent)
        self.db = db
        self._build()

    def _build(self):
        layout = QVBoxLayout(self)
        layout.setContentsMargins(12, 12, 12, 12)
        layout.setSpacing(8)

        # Toolbar
        toolbar = QHBoxLayout()

        self._search = QLineEdit()
        self._search.setPlaceholderText("Tìm theo tên...")
        self._search.setFixedHeight(32)
        self._search.textChanged.connect(self._filter)
        toolbar.addWidget(self._search)

        scan_btn = QPushButton("Quét lại")
        scan_btn.setFixedHeight(32)
        scan_btn.setStyleSheet("background: #546e7a; color: white; border-radius: 4px; padding: 0 12px;")
        scan_btn.clicked.connect(self._scan)
        toolbar.addWidget(scan_btn)

        allow_all = QPushButton("Cho phép tất cả")
        allow_all.setFixedHeight(32)
        allow_all.setStyleSheet("background: #388e3c; color: white; border-radius: 4px; padding: 0 12px;")
        allow_all.clicked.connect(self._allow_all)
        toolbar.addWidget(allow_all)

        block_all = QPushButton("Chặn tất cả")
        block_all.setFixedHeight(32)
        block_all.setStyleSheet("background: #c62828; color: white; border-radius: 4px; padding: 0 12px;")
        block_all.clicked.connect(self._block_all)
        toolbar.addWidget(block_all)

        layout.addLayout(toolbar)

        # Table
        self._table = QTableWidget()
        self._table.setColumnCount(5)
        self._table.setHorizontalHeaderLabels([
            "Ứng dụng", "Danh mục", "Đã dùng hôm nay", "Giới hạn thời gian", "Cho phép"
        ])
        self._table.horizontalHeader().setSectionResizeMode(0, QHeaderView.ResizeMode.Stretch)
        self._table.horizontalHeader().setSectionResizeMode(1, QHeaderView.ResizeMode.ResizeToContents)
        self._table.horizontalHeader().setSectionResizeMode(2, QHeaderView.ResizeMode.ResizeToContents)
        self._table.horizontalHeader().setSectionResizeMode(3, QHeaderView.ResizeMode.ResizeToContents)
        self._table.horizontalHeader().setSectionResizeMode(4, QHeaderView.ResizeMode.ResizeToContents)
        self._table.setSelectionMode(QAbstractItemView.SelectionMode.NoSelection)
        self._table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self._table.setAlternatingRowColors(True)
        self._table.verticalHeader().setVisible(False)
        self._table.setShowGrid(False)
        layout.addWidget(self._table)

        self._all_apps = []
        self.refresh()

    def refresh(self):
        self._all_apps = self.db.get_all_apps()
        self._usage = self.db.get_today_usage_including_open()
        self._filter(self._search.text())

    def _filter(self, query: str):
        q = query.lower()
        filtered = [a for a in self._all_apps if q in a.name.lower() or q in a.categories.lower()] if q else self._all_apps
        self._populate(filtered)

    def _populate(self, apps):
        self._table.setRowCount(0)
        self._table.setRowCount(len(apps))

        for row, app in enumerate(apps):
            # Name
            name_item = QTableWidgetItem(app.name)
            name_item.setData(Qt.ItemDataRole.UserRole, app.desktop_id)
            name_item.setToolTip(app.exec_binary)
            self._table.setItem(row, 0, name_item)

            # Category
            cat = app.categories.split(";")[0] if app.categories else "-"
            self._table.setItem(row, 1, QTableWidgetItem(cat))

            # Usage
            used = self._usage.get(app.desktop_id, 0)
            usage_str = self._fmt_time(used)
            lim = _effective_limit(app)
            if lim > 0:
                usage_str += f" / {lim}p"
            self._table.setItem(row, 2, QTableWidgetItem(usage_str))

            # Schedule button
            sched_btn = QPushButton(_schedule_label(app))
            sched_btn.setProperty("desktop_id", app.desktop_id)
            sched_btn.clicked.connect(self._on_schedule_btn)
            self._table.setCellWidget(row, 3, sched_btn)

            # Allow checkbox
            chk = QCheckBox()
            chk.setChecked(app.allowed)
            chk.setProperty("desktop_id", app.desktop_id)
            chk.stateChanged.connect(self._on_allowed_changed)
            container = QWidget()
            hb = QHBoxLayout(container)
            hb.addWidget(chk)
            hb.setAlignment(Qt.AlignmentFlag.AlignCenter)
            hb.setContentsMargins(0, 0, 0, 0)
            self._table.setCellWidget(row, 4, container)

        self._table.setRowHeight

    def _fmt_time(self, secs: float) -> str:
        if secs < 60:
            return f"{int(secs)}s"
        mins = int(secs // 60)
        s = int(secs % 60)
        return f"{mins}p {s}s" if s else f"{mins}p"

    def _on_allowed_changed(self, state):
        chk = self.sender()
        desktop_id = chk.property("desktop_id")
        self.db.set_app_allowed(desktop_id, bool(state))

    def _on_schedule_btn(self):
        btn = self.sender()
        desktop_id = btn.property("desktop_id")
        app = self.db.get_app(desktop_id)
        if not app:
            return
        dlg = ScheduleDialog(app.name, app.daily_limit_minutes, app.limit_schedule, self)
        if dlg.exec() == QDialog.DialogCode.Accepted:
            new_limit, new_schedule = dlg.get_result()
            self.db.set_app_schedule(desktop_id, new_limit, new_schedule)
            self.refresh()

    def _scan(self):
        count = scan_desktop_files(self.db)
        self.refresh()
        QMessageBox.information(self, "Quét xong", f"Đã tìm thấy {count} ứng dụng.")

    def _allow_all(self):
        if QMessageBox.question(self, "Xác nhận", "Cho phép tất cả ứng dụng?") == QMessageBox.StandardButton.Yes:
            for app in self._all_apps:
                self.db.set_app_allowed(app.desktop_id, True)
            self.refresh()

    def _block_all(self):
        if QMessageBox.question(self, "Xác nhận", "Chặn tất cả ứng dụng?") == QMessageBox.StandardButton.Yes:
            for app in self._all_apps:
                self.db.set_app_allowed(app.desktop_id, False)
            self.refresh()


class _BarChartWidget(QWidget):
    """Simple bar chart drawn with QPainter — no extra dependencies."""

    def __init__(self, data: list[tuple[str, float]], parent=None):
        super().__init__(parent)
        self._data = data  # [(label, value_in_minutes), ...]
        self.setMinimumSize(640, 320)

    def paintEvent(self, event):
        if not self._data:
            return
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)

        ml, mr, mt, mb = 55, 20, 20, 52  # margins
        W, H = self.width(), self.height()
        cw, ch = W - ml - mr, H - mt - mb  # chart area

        max_val = max((v for _, v in self._data), default=1) or 1
        n = len(self._data)
        slot_w = cw / n
        bar_w = max(4, slot_w * 0.65)

        # Grid lines + Y labels
        grid_pen = QPen(QColor("#e0e0e0"))
        for i in range(5):
            val = max_val * i / 4
            y = mt + ch - (val / max_val) * ch
            p.setPen(grid_pen)
            p.drawLine(ml, int(y), ml + cw, int(y))
            p.setPen(QColor("#666"))
            label = f"{val:.0f}p" if val >= 1 else f"{val*60:.0f}s"
            p.drawText(0, int(y) - 8, ml - 4, 16,
                       Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter, label)

        # Bars
        for i, (label, val) in enumerate(self._data):
            x = ml + i * slot_w + (slot_w - bar_w) / 2
            bh = (val / max_val) * ch if max_val > 0 else 0
            y = mt + ch - bh

            # Bar fill
            p.setPen(Qt.PenStyle.NoPen)
            p.setBrush(QBrush(QColor("#1565c0")))
            p.drawRoundedRect(int(x), int(y), int(bar_w), int(bh), 3, 3)

            # Value label above bar
            if val > 0:
                p.setPen(QColor("#333"))
                txt = f"{val:.0f}" if val >= 1 else f"{val*60:.0f}s"
                p.drawText(int(x) - 4, int(y) - 16, int(bar_w) + 8, 14,
                           Qt.AlignmentFlag.AlignHCenter, txt)

            # X-axis label (rotate if too many bars)
            p.setPen(QColor("#444"))
            p.save()
            if n > 12:
                p.translate(int(x + bar_w / 2), mt + ch + 6)
                p.rotate(40)
                p.drawText(0, 0, label)
            else:
                p.drawText(int(x) - 4, mt + ch + 4, int(bar_w) + 8, 44,
                           Qt.AlignmentFlag.AlignHCenter | Qt.AlignmentFlag.AlignTop, label)
            p.restore()

        # Axes
        p.setPen(QPen(QColor("#333"), 1))
        p.drawLine(ml, mt, ml, mt + ch)
        p.drawLine(ml, mt + ch, ml + cw, mt + ch)
        p.end()


class UsageGraphDialog(QDialog):
    def __init__(self, db, desktop_id: str, app_name: str, days: int, parent=None):
        super().__init__(parent)
        self.setWindowTitle(f"Thống kê: {app_name}")
        self.setMinimumSize(720, 460)
        self.resize(760, 480)

        layout = QVBoxLayout(self)
        layout.setSpacing(8)

        title = QLabel(f"<b>{app_name}</b>")
        title.setAlignment(Qt.AlignmentFlag.AlignCenter)
        f = QFont(); f.setPointSize(13)
        title.setFont(f)
        layout.addWidget(title)

        if days == 0:
            hourly = db.get_hourly_usage_today(desktop_id)
            data = [(f"{h}h", hourly[h] / 60) for h in range(24)]
            subtitle = "Hôm nay — theo giờ (phút)"
        else:
            daily = db.get_daily_usage_for_app(desktop_id, days)
            data = [(d[5:], daily[d] / 60) for d in sorted(daily)]  # MM-DD
            subtitle = f"{days} ngày gần nhất — theo ngày (phút)"

        sub = QLabel(subtitle)
        sub.setAlignment(Qt.AlignmentFlag.AlignCenter)
        sub.setStyleSheet("color: #888; font-size: 9pt;")
        layout.addWidget(sub)

        layout.addWidget(_BarChartWidget(data))

        close_btn = QPushButton("Đóng")
        close_btn.setFixedWidth(100)
        close_btn.clicked.connect(self.accept)
        layout.addWidget(close_btn, alignment=Qt.AlignmentFlag.AlignCenter)


class HistoryTab(QWidget):
    def __init__(self, db: Database, parent=None):
        super().__init__(parent)
        self.db = db
        self._current_days = 7
        self._build()

    def _build(self):
        layout = QVBoxLayout(self)
        layout.setContentsMargins(12, 12, 12, 12)
        layout.setSpacing(8)

        toolbar = QHBoxLayout()
        toolbar.addWidget(QLabel("Xem:"))

        self._days_btns = []
        for label, days in [("Hôm nay", 0), ("3 ngày", 3), ("7 ngày", 7), ("14 ngày", 14), ("30 ngày", 30)]:
            btn = QPushButton(label)
            btn.setFixedHeight(30)
            btn.setCheckable(True)
            btn.setProperty("days", days)
            btn.clicked.connect(self._on_days_btn)
            self._days_btns.append(btn)
            toolbar.addWidget(btn)

        toolbar.addStretch()
        self._total_label = QLabel("")
        self._total_label.setStyleSheet("color: #1565c0; font-weight: bold; font-size: 10pt;")
        toolbar.addWidget(self._total_label)

        self._days_btns[2].setChecked(True)  # default 7 ngày
        layout.addLayout(toolbar)

        self._table = QTableWidget()
        self._table.setColumnCount(3)
        self._table.setHorizontalHeaderLabels(["Ngày", "Ứng dụng", "Thời gian (phút)"])
        self._table.horizontalHeader().setSectionResizeMode(0, QHeaderView.ResizeMode.ResizeToContents)
        self._table.horizontalHeader().setSectionResizeMode(1, QHeaderView.ResizeMode.Stretch)
        self._table.horizontalHeader().setSectionResizeMode(2, QHeaderView.ResizeMode.ResizeToContents)
        self._table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self._table.setAlternatingRowColors(True)
        self._table.verticalHeader().setVisible(False)
        self._table.setToolTip("Double-click để xem biểu đồ chi tiết")
        self._table.cellDoubleClicked.connect(self._on_double_click)
        layout.addWidget(self._table)

        self.refresh(7)

    def _on_days_btn(self):
        btn = self.sender()
        for b in self._days_btns:
            b.setChecked(b is btn)
        self._current_days = btn.property("days")
        self.refresh(self._current_days)

    def _on_double_click(self, row: int, _col: int):
        item = self._table.item(row, 1)
        if not item:
            return
        desktop_id = item.data(Qt.ItemDataRole.UserRole)
        app_name = item.text()
        if desktop_id:
            dlg = UsageGraphDialog(self.db, desktop_id, app_name, self._current_days, self)
            dlg.exec()

    def refresh(self, days: int = 7):
        self._current_days = days
        app_map = {a.desktop_id: a.name for a in self.db.get_all_apps()}
        history = self.db.get_usage_history(days)

        total_secs = sum(r["total_seconds"] for r in history)
        total_mins = total_secs / 60
        if total_mins >= 60:
            h, m = int(total_mins // 60), int(total_mins % 60)
            total_str = f"{h} giờ {m} phút" if m else f"{h} giờ"
        else:
            total_str = f"{total_mins:.0f} phút"
        self._total_label.setText(f"Tổng: {total_str}")

        self._table.setRowCount(len(history))
        for row, rec in enumerate(history):
            date_item = QTableWidgetItem(rec["session_date"])
            self._table.setItem(row, 0, date_item)

            name = app_map.get(rec["desktop_id"], rec["desktop_id"])
            name_item = QTableWidgetItem(name)
            name_item.setData(Qt.ItemDataRole.UserRole, rec["desktop_id"])
            self._table.setItem(row, 1, name_item)

            mins = round(rec["total_seconds"] / 60, 1)
            self._table.setItem(row, 2, QTableWidgetItem(str(mins)))


class SettingsTab(QWidget):
    def __init__(self, db: Database, parent=None):
        super().__init__(parent)
        self.db = db
        self._build()

    def _build(self):
        layout = QVBoxLayout(self)
        layout.setContentsMargins(20, 16, 20, 16)
        layout.setSpacing(16)
        layout.setAlignment(Qt.AlignmentFlag.AlignTop)

        # Control group
        ctrl_group = QGroupBox("Kiểm soát")
        ctrl_layout = QVBoxLayout(ctrl_group)

        enabled_row = QHBoxLayout()
        self._enabled_chk = QCheckBox("Bật kiểm soát ứng dụng")
        self._enabled_chk.setChecked(self.db.get_setting("enabled", "0") == "1")
        enabled_row.addWidget(self._enabled_chk)
        enabled_row.addStretch()
        ctrl_layout.addLayout(enabled_row)
        hint1 = QLabel("Khi tắt, tất cả ứng dụng đều được phép chạy (chỉ theo dõi thời gian)")
        hint1.setStyleSheet("color: #888; font-size: 9pt;")
        ctrl_layout.addWidget(hint1)

        ctrl_layout.addSpacing(8)

        default_row = QHBoxLayout()
        self._default_chk = QCheckBox("Mặc định cho phép ứng dụng mới")
        self._default_chk.setChecked(self.db.get_setting("default_allow", "0") == "1")
        default_row.addWidget(self._default_chk)
        default_row.addStretch()
        ctrl_layout.addLayout(default_row)
        hint2 = QLabel("Khi phát hiện ứng dụng mới, tự động cho phép thay vì chặn")
        hint2.setStyleSheet("color: #888; font-size: 9pt;")
        ctrl_layout.addWidget(hint2)

        layout.addWidget(ctrl_group)

        # Kid user group
        kid_group = QGroupBox("Tài khoản của con")
        kid_layout = QFormLayout(kid_group)
        self._kid_user = QLineEdit()
        self._kid_user.setText(self.db.get_setting("kid_user", ""))
        self._kid_user.setPlaceholderText("ví dụ: kid, con, ...")
        self._kid_user.setFixedWidth(200)
        kid_layout.addRow("Linux username:", self._kid_user)
        hint_kid = QLabel("Tên tài khoản Linux của con. Daemon sẽ giám sát tài khoản này.")
        hint_kid.setStyleSheet("color: #888; font-size: 9pt;")
        kid_layout.addRow(hint_kid)
        layout.addWidget(kid_group)

        # Password group
        pwd_group = QGroupBox("Đổi mật khẩu")
        pwd_layout = QFormLayout(pwd_group)
        self._new_pwd = QLineEdit()
        self._new_pwd.setEchoMode(QLineEdit.EchoMode.Password)
        self._new_pwd.setPlaceholderText("Để trống nếu không đổi")
        self._new_pwd.setFixedWidth(240)
        self._confirm_pwd = QLineEdit()
        self._confirm_pwd.setEchoMode(QLineEdit.EchoMode.Password)
        self._confirm_pwd.setPlaceholderText("Nhập lại mật khẩu mới")
        self._confirm_pwd.setFixedWidth(240)
        pwd_layout.addRow("Mật khẩu mới:", self._new_pwd)
        pwd_layout.addRow("Xác nhận:", self._confirm_pwd)
        layout.addWidget(pwd_group)

        save_btn = QPushButton("Lưu cài đặt")
        save_btn.setFixedHeight(36)
        save_btn.setFixedWidth(140)
        save_btn.setStyleSheet(
            "QPushButton { background: #1565c0; color: white; border-radius: 6px; font-weight: bold; }"
            "QPushButton:hover { background: #0d47a1; }"
        )
        save_btn.clicked.connect(self._save)
        layout.addWidget(save_btn)

    def _save(self):
        self.db.set_setting("enabled", "1" if self._enabled_chk.isChecked() else "0")
        self.db.set_setting("default_allow", "1" if self._default_chk.isChecked() else "0")
        self.db.set_setting("kid_user", self._kid_user.text().strip())

        new_pwd = self._new_pwd.text().strip()
        confirm = self._confirm_pwd.text().strip()
        if new_pwd:
            if new_pwd != confirm:
                QMessageBox.warning(self, "Lỗi", "Mật khẩu không khớp.")
                return
            self.db.set_password(new_pwd)
            self._new_pwd.clear()
            self._confirm_pwd.clear()

        QMessageBox.information(self, "Thành công", "Đã lưu cài đặt.")


class AdminWindow(QMainWindow):
    def __init__(self, db: Database, parent=None):
        super().__init__(parent)
        self.db = db
        self.setWindowTitle("Screen Time - Quản lý")
        self.setMinimumSize(800, 560)
        self._authenticated = False
        self._build()

        # Auto-refresh every 10 seconds when visible
        self._refresh_timer = QTimer(self)
        self._refresh_timer.timeout.connect(self._auto_refresh)
        self._refresh_timer.start(10000)

    def _build(self):
        self._stack = QStackedWidget()
        self.setCentralWidget(self._stack)
        self.setStyleSheet("QMainWindow { background: #f5f5f5; }")

        # Page 0: Login
        self._login_widget = LoginWidget(self.db, self._on_login_success)
        self._login_widget.setStyleSheet("background: #f5f5f5;")
        self._stack.addWidget(self._login_widget)

        # Page 1: Admin tabs
        admin = QWidget()
        admin_layout = QVBoxLayout(admin)
        admin_layout.setContentsMargins(0, 0, 0, 0)

        # Header
        header = QWidget()
        header.setFixedHeight(52)
        header.setStyleSheet("background: #1565c0;")
        hbox = QHBoxLayout(header)
        hbox.setContentsMargins(16, 0, 16, 0)
        title = QLabel("Screen Time")
        tf = QFont(); tf.setPointSize(14); tf.setBold(True)
        title.setFont(tf)
        title.setStyleSheet("color: white;")
        hbox.addWidget(title)
        hbox.addStretch()
        lock_btn = QPushButton("Khoá")
        lock_btn.setFixedHeight(30)
        lock_btn.setStyleSheet(
            "QPushButton { background: rgba(255,255,255,0.2); color: white; border-radius: 4px; padding: 0 12px; }"
            "QPushButton:hover { background: rgba(255,255,255,0.35); }"
        )
        lock_btn.clicked.connect(self._lock)
        hbox.addWidget(lock_btn)
        admin_layout.addWidget(header)

        # Tabs
        self._tabs = QTabWidget()
        self._tabs.setStyleSheet(
            "QTabWidget::pane { border: none; }"
            "QTabBar::tab { padding: 8px 20px; font-size: 10pt; }"
            "QTabBar::tab:selected { border-bottom: 2px solid #1565c0; color: #1565c0; font-weight: bold; }"
        )
        self._apps_tab = AppsTab(self.db)
        self._history_tab = HistoryTab(self.db)
        self._settings_tab = SettingsTab(self.db)
        self._tabs.addTab(self._apps_tab, "Ứng dụng")
        self._tabs.addTab(self._history_tab, "Lịch sử")
        self._tabs.addTab(self._settings_tab, "Cài đặt")
        self._tabs.currentChanged.connect(self._on_tab_changed)
        admin_layout.addWidget(self._tabs)

        self._stack.addWidget(admin)

    def _on_login_success(self):
        self._authenticated = True
        self._stack.setCurrentIndex(1)
        self._apps_tab.refresh()

    def _lock(self):
        self._authenticated = False
        self._stack.setCurrentIndex(0)

    def _on_tab_changed(self, idx):
        if idx == 0:
            self._apps_tab.refresh()
        elif idx == 1:
            self._history_tab.refresh()

    def _auto_refresh(self):
        if self.isVisible() and self._authenticated:
            if self._tabs.currentIndex() == 0:
                self._apps_tab.refresh()

    def closeEvent(self, event):
        self._lock()
        self.hide()
        event.ignore()

    def open_and_raise(self):
        self.show()
        self.raise_()
        self.activateWindow()
        if not self._authenticated:
            self._stack.setCurrentIndex(0)
