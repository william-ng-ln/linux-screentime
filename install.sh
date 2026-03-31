#!/bin/bash
set -e

DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
INSTALL_DIR="/opt/screentime"

echo "=== Screen Time - Cài đặt ==="
echo ""

# Must be run as root
if [ "$EUID" -ne 0 ]; then
  echo "[!] Vui lòng chạy với quyền root: sudo ./install.sh"
  exit 1
fi

# Who is the parent user running this script
PARENT_USER="${SUDO_USER:-$(logname 2>/dev/null || echo "")}"
if [ -z "$PARENT_USER" ]; then
  echo "[!] Không xác định được tài khoản phụ huynh. Chạy bằng: sudo ./install.sh"
  exit 1
fi
PARENT_HOME=$(getent passwd "$PARENT_USER" | cut -d: -f6)

echo "  Phụ huynh  : $PARENT_USER"
echo "  Cài vào    : $INSTALL_DIR"
echo ""

# Check PyQt6 is available (system package)
python3 -c "from PyQt6.QtWidgets import QApplication" 2>/dev/null || {
  echo "[!] PyQt6 chưa được cài. Chạy: sudo pacman -S python-pyqt6"
  exit 1
}

# Stop existing services before copying (so files are not in use)
systemctl stop screentime-daemon.service 2>/dev/null || true

# Copy app files to /opt/screentime
echo "[1/6] Sao chép files vào $INSTALL_DIR..."
mkdir -p "$INSTALL_DIR"
# Clean old install then copy fresh
find "$INSTALL_DIR" -mindepth 1 -maxdepth 1 \
  ! -name '.venv' ! -name 'screentime.db' -exec rm -rf {} +
cp -r "$DIR/screentime" "$DIR/main.py" "$DIR/daemon.py" \
      "$DIR/screentime.service" "$DIR/screentime-daemon.service" \
      "$DIR/screentime-admin.desktop" \
      "$INSTALL_DIR/"

# Create venv inside /opt/screentime
echo "[2/6] Tạo virtual environment..."
if [ ! -d "$INSTALL_DIR/.venv" ]; then
  python3 -m venv --system-site-packages "$INSTALL_DIR/.venv"
fi
"$INSTALL_DIR/.venv/bin/pip" install -q psutil

# Create DB directory — world-writable so any user can run the admin app
echo "[3/6] Tạo thư mục dữ liệu..."
mkdir -p /var/lib/screentime
chmod 777 /var/lib/screentime
[ -f /var/lib/screentime/screentime.db ] && chmod 666 /var/lib/screentime/screentime.db || true
[ -f /var/lib/screentime/screentime.db-shm ] && chmod 666 /var/lib/screentime/screentime.db-shm || true

# Install system daemon service
echo "[4/6] Cài đặt system daemon service..."
sed -e "s|__INSTALL_DIR__|$INSTALL_DIR|g" \
    "$INSTALL_DIR/screentime-daemon.service" > /etc/systemd/system/screentime-daemon.service
systemctl daemon-reload
systemctl enable --now screentime-daemon.service

# Install admin GUI service for parent user
echo "[5/6] Cài đặt admin GUI service cho $PARENT_USER..."
SYSTEMD_USER_DIR="$PARENT_HOME/.config/systemd/user"
mkdir -p "$SYSTEMD_USER_DIR"
sed -e "s|__INSTALL_DIR__|$INSTALL_DIR|g" \
    "$INSTALL_DIR/screentime.service" > "$SYSTEMD_USER_DIR/screentime.service"
chown -R "$PARENT_USER:$PARENT_USER" "$SYSTEMD_USER_DIR"
sudo -u "$PARENT_USER" XDG_RUNTIME_DIR="/run/user/$(id -u "$PARENT_USER")" \
  systemctl --user daemon-reload
sudo -u "$PARENT_USER" XDG_RUNTIME_DIR="/run/user/$(id -u "$PARENT_USER")" \
  systemctl --user enable --now screentime.service

# Install .desktop file globally (and remove any old per-user copy)
echo "[6/6] Cài đặt shortcut start menu..."
rm -f "$PARENT_HOME/.local/share/applications/screentime-admin.desktop"
mkdir -p /usr/local/share/applications
sed -e "s|__INSTALL_DIR__|$INSTALL_DIR|g" \
    "$INSTALL_DIR/screentime-admin.desktop" > /usr/local/share/applications/screentime-admin.desktop

echo ""
echo "=== Cài đặt hoàn tất ==="
echo ""
echo "  App đã cài tại   : $INSTALL_DIR"
echo "  Daemon (root)    : đang chạy"
echo "  Admin UI         : chạy trong session của $PARENT_USER (system tray)"
echo "  Mật khẩu mặc định: admin"
echo "  => Vào Settings trong admin UI để đổi mật khẩu và nhập tài khoản của con!"
echo ""
echo "Các lệnh hữu ích:"
echo "  systemctl status screentime-daemon          # trạng thái daemon"
echo "  journalctl -u screentime-daemon -f          # log daemon"
echo "  sudo systemctl restart screentime-daemon    # khởi động lại daemon"
echo ""
echo "  systemctl --user status screentime          # trạng thái admin UI"
echo "  journalctl --user -u screentime -f          # log admin UI"
