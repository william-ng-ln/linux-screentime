# linux-screentime — Ghi chú kỹ thuật

Chương trình kiểm soát thời gian sử dụng máy tính cho trẻ em, chạy trên Arch Linux / KDE Plasma 6.

---

## Mục tiêu

- Chỉ cho phép chạy các ứng dụng trong danh sách được phép (allowlist)
- Giới hạn thời gian sử dụng theo ngày, có thể đặt lịch theo từng ngày trong tuần
- Giao diện quản trị Qt cho bố mẹ (không phải web)
- Daemon chạy với quyền root, theo dõi tài khoản của con

---

## Kiến trúc

### Hai tiến trình riêng biệt

| File | Chạy với quyền | Mô tả |
|---|---|---|
| `daemon.py` | root (systemd system service) | Giám sát tiến trình, kill app vi phạm |
| `main.py` | parent user (systemd user service) | Qt admin GUI, system tray |

### Database (root-only)
- SQLite tại `/var/lib/screentime/screentime.db`
- Permissions: file `600`, thư mục `755` — chỉ root đọc/ghi được; con không thể xóa hay đọc file DB
- **Không dùng WAL mode** — WAL tạo file `-shm` owned by root, user khác không ghi được

### IPC (admin GUI ↔ daemon)
- Admin GUI **không** truy cập DB trực tiếp — giao tiếp qua Unix socket `/run/screentime/control.sock`
- Socket: `chmod 0666` (ai cũng kết nối được), bảo mật ở tầng protocol
- **Token auth**: gọi `authenticate(password)` → nhận token ngẫu nhiên 64-char, TTL 8 giờ sliding
- Lệnh đọc (get_all_apps, get_usage_history, …): không cần token
- Lệnh ghi (set_app_allowed, set_setting, scan_apps, …): phải có token hợp lệ
- `set_password` → revoke tất cả token hiện có
- `AdminClient.logout()` được gọi khi lock cửa sổ admin → xóa token khỏi memory

**Files IPC:**
| File | Mô tả |
|---|---|
| `screentime/ipc.py` | Hằng số chung: SOCKET_PATH, TOKEN_TTL, READ_COMMANDS, WRITE_COMMANDS |
| `screentime/ipc_server.py` | Thread trong daemon: nhận request, dispatch DB, quản lý token |
| `screentime/ipc_client.py` | AdminClient: drop-in thay thế Database trong admin GUI |

### Cài đặt
- Cài vào `/opt/screentime/`
- Venv: `/opt/screentime/.venv` với `--system-site-packages` (cần PyQt6 từ system)
- Script cài: `install.sh`

---

## File quan trọng

```
daemon.py                          # Root daemon entry point
main.py                            # Qt admin GUI entry point
install.sh                         # Cài đặt vào /opt/screentime
screentime-daemon.service          # systemd system service
screentime.service                 # systemd user service
screentime-admin.desktop           # .desktop file (toàn hệ thống, /usr/share/applications)

screentime/
  config.py                        # TARGET_USER, DB_PATH, POLL_INTERVAL, SYSTEM_PROCESS_NAMES
  database.py                      # SQLite wrapper, AppRecord dataclass (dùng bởi daemon)
  enforcer.py                      # Thread giám sát + kill tiến trình
  time_tracker.py                  # Theo dõi thời gian dùng app theo session
  desktop_scanner.py               # Parse .desktop files, resolve shell script wrappers
  notifier.py                      # DaemonNotifier — gửi notify-send đến màn hình của con
  overlay.py                       # Fullscreen notification (chạy như subprocess dưới quyền kid)
  ipc.py                           # Hằng số IPC chung (SOCKET_PATH, token TTL, command sets)
  ipc_server.py                    # IpcServer thread trong daemon — nhận request, dispatch DB
  ipc_client.py                    # AdminClient — drop-in thay Database trong admin GUI
  ui/
    admin_window.py                # AdminWindow (login + tabs), ScheduleDialog, AppsTab, HistoryTab
    tray.py                        # System tray icon
```

---

## Database schema

### Bảng `applications`
| Cột | Ý nghĩa |
|---|---|
| `desktop_id` | Stem của .desktop file (e.g. `org.kde.konsole`) |
| `name` | Tên hiển thị |
| `exec_binary` | Đường dẫn thực thi đã resolve (follow symlinks + shell scripts) |
| `exec_args` | Cho app dùng chung binary (waydroid): package name để match cmdline |
| `allowed` | 0/1 — có trong allowlist không |
| `daily_limit_minutes` | Giới hạn phút/ngày (0 = không giới hạn) |
| `limit_schedule` | Lịch theo tuần: `"30,30,30,30,30,60,60"` (T2→CN), `""` = dùng `daily_limit_minutes` |

### Bảng `settings`
| Key | Mặc định | Ý nghĩa |
|---|---|---|
| `enabled` | `"0"` | Bật/tắt enforcement (tắt = chỉ theo dõi, không kill) |
| `default_allow` | `"0"` | App mới tự động được phép hay không |
| `kid_user` | `""` | Linux username của con (phải cấu hình trước khi daemon hoạt động) |
| `admin_password_hash` | hash("admin") | PBKDF2-SHA256, salt cố định |

### Bảng `usage_sessions`
- Mỗi PID mỗi app tạo một session khi bắt đầu, đóng khi kết thúc
- `close_stale_sessions()` gọi khi daemon khởi động để đóng session cũ

---

## Enforcer — logic giám sát

**File:** `screentime/enforcer.py`

- Poll mỗi `POLL_INTERVAL` giây (mặc định 2s)
- Với mỗi tiến trình của `TARGET_USER`:
  1. Bỏ qua nếu tên nằm trong `SYSTEM_PROCESS_NAMES` (KDE, systemd, input method, v.v.)
  2. Tìm app tương ứng qua `_get_app_for_process()` — 5 bước match (xem bên dưới)
  3. Nếu `app.allowed == False` → kill + notify
  4. Nếu có giới hạn thời gian → kiểm tra `_get_today_limit_minutes(app)` (schedule-aware)
  5. Cảnh báo khi còn 5 phút (`WARN_MINUTES = 5`), kill khi hết giờ

**5 bước match tiến trình → app:**
1. Exact match đường dẫn exe
2. Realpath match (follow symlinks)
3. Basename match
4. Stem match trong cùng thư mục (`soffice` ↔ `soffice.bin`)
5. Cmdline match (dành cho app dùng chung binary, e.g. waydroid)

**Waydroid:** `_kill()` gọi `waydroid app stop <package>` trước khi SIGKILL process

**Per-day schedule:** `_get_today_limit_minutes(app)` ưu tiên `limit_schedule` (split bằng `,`, lấy index `weekday()`), fallback về `daily_limit_minutes`.

---

## Desktop scanner

**File:** `screentime/desktop_scanner.py`

- Quét tất cả `.desktop` files từ XDG dirs
- Lọc theo `USER_FACING_CATEGORIES` (bỏ qua system utilities)
- `EXCLUDED_DESKTOP_IDS`: blacklist cứng một số app hệ thống (bssh, fcitx5, v.v.)
- **Resolve shell script wrappers:** `_resolve_script_target()` follow `exec` chain trong bash scripts, thay thế `$HERE`, `${HERE}`, `$VAR/` bằng thư mục script
- **Waydroid:** Nếu binary là `waydroid`, extract package name từ `Exec=` vào `exec_args`

---

## Admin GUI

**File:** `screentime/ui/admin_window.py`

### Tabs
- **Ứng dụng:** Bảng app với checkbox cho phép + nút "Giới hạn thời gian" mở `ScheduleDialog`
- **Lịch sử:** Biểu đồ sử dụng theo ngày/tuần, double-click mở popup biểu đồ chi tiết
- **Cài đặt:** Bật/tắt kiểm soát, cấu hình tài khoản con, đổi mật khẩu

### ScheduleDialog
- 3 chế độ: Không giới hạn / Mỗi ngày (1 spinbox) / Theo ngày trong tuần (7 spinboxes T2→CN)
- Lưu vào `limit_schedule` hoặc `daily_limit_minutes` tùy chế độ

### Bảo mật
- Cửa sổ bắt đầu ở trang login
- Đóng cửa sổ (✕) → tự động lock về login screen
- Single-instance: PID file + SIGUSR1

### Icon
- Icon đồng hồ vẽ bằng QPainter (không cần file ảnh), set qua `app.setWindowIcon()`

---

## Notifier (daemon → màn hình con)

**File:** `screentime/notifier.py`

- `DaemonNotifier` duck-type Qt signal interface (`_FakeSignal`)
- Đọc `DISPLAY`/`WAYLAND_DISPLAY` từ `/proc/<kid_pid>/environ`
- Chạy `notify-send` dưới quyền kid user qua `sudo -u <kid>`

---

## Các lỗi đã gặp và cách fix

| Lỗi | Nguyên nhân | Fix |
|---|---|---|
| Enforcer kill VS Code | Default `allowed=0` cho tất cả app | Thêm `enabled="0"` default — không enforce cho đến khi bố mẹ cấu hình |
| DB readonly cho admin app | WAL tạo `-shm` owned by root | Bỏ `PRAGMA journal_mode=WAL` |
| Konsole time tăng mãi | Stale sessions từ lần chạy trước | `close_stale_sessions()` khi daemon start |
| Chrome không bị block | `google-chrome-stable` là shell script → shell script → ELF | `_resolve_script_target()` follow chain |
| LibreOffice không bị block | Script dùng `$sd_prog/soffice.bin` | Thay `$VAR/` bằng script dir trong resolver |
| Waydroid app không bị block | Tất cả waydroid app dùng chung `/usr/bin/waydroid` | Lưu package name vào `exec_args`, match qua cmdline |
| Daemon monitor "root" | `config.TARGET_USER` lấy `$USER` = "root" khi chạy với sudo | Đọc `kid_user` từ DB thay vì env |

---

## Cài đặt và vận hành

```bash
# Cài đặt / update
sudo ./install.sh

# Daemon (root)
sudo systemctl status screentime-daemon
sudo journalctl -u screentime-daemon -f

# Admin GUI (parent user)
systemctl --user status screentime
journalctl --user -u screentime -f

# Cấu hình lần đầu:
# 1. Mở admin GUI từ system tray
# 2. Vào Cài đặt → nhập Linux username của con → Lưu
# 3. Daemon sẽ tự nhận username sau tối đa 30s
# 4. Vào tab Ứng dụng → cho phép các app cần thiết
# 5. Cài đặt → Bật kiểm soát ứng dụng
```

---

## App icon

File `screentime.svg` trong project root. Install.sh copy vào `/usr/share/icons/hicolor/scalable/apps/screentime.svg` và chạy `gtk-update-icon-cache`.

Thứ tự ưu tiên load icon (cả tray lẫn window):
1. `QIcon.fromTheme("screentime")` — sau khi `install.sh` chạy
2. File SVG cạnh `main.py` / package root — khi chạy từ source
3. Icon vẽ bằng QPainter — fallback cuối cùng

## Overlay thông báo

File `screentime/overlay.py` — standalone Qt script (không import từ package screentime).

- Được `DaemonNotifier` launch như subprocess với env của kid user
- Python: `<install_dir>/.venv/bin/python3`, fallback về system `python3`
- Nhận args: `--type blocked|time_up` và `--app "App Name"`
- Hiển thị fullscreen với nền tối + card màu (đỏ = blocked, cam = time_up)
- Countdown 6 giây → nút "Đã hiểu" mới active → auto-close sau 2 giây
- Chặn Alt+F4 / keyboard dismiss trong lúc đang đếm ngược

**Warn (5 phút còn lại):** vẫn dùng `notify-send` (ít gây phiền hơn)

**Cooldown debounce:** `DaemonNotifier._last_overlay` track thời gian notify cuối cùng theo app — không hiện overlay lặp lại trong vòng 30 giây (`_OVERLAY_COOLDOWN`)

## Trạng thái hiện tại (2026-03-31)

Đã hoàn thành:
- [x] Daemon root giám sát tiến trình của con
- [x] Allowlist + kill vi phạm
- [x] Giới hạn thời gian hàng ngày
- [x] Lịch thời gian theo từng ngày trong tuần (ScheduleDialog)
- [x] Theo dõi và hiển thị lịch sử sử dụng (biểu đồ)
- [x] Cảnh báo 5 phút trước khi hết giờ (notify-send)
- [x] Fullscreen overlay khi bị block / hết giờ
- [x] Waydroid app support (cmdline matching + `waydroid app stop`)
- [x] Resolve shell script wrappers (Chrome, LibreOffice, v.v.)
- [x] App icon SVG + theme install, dùng thống nhất ở tray + window + .desktop
- [x] Lock khi đóng cửa sổ admin

Có thể cải thiện thêm:
- Cho phép con tự xin thêm giờ (yêu cầu bố mẹ confirm)
- Giới hạn thời gian theo tổng (tất cả app cộng lại), không chỉ per-app
