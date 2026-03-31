from pathlib import Path
import os

DB_PATH = Path("/var/lib/screentime/screentime.db")
WEB_PORT = 8770
WEB_HOST = "127.0.0.1"
TARGET_USER = os.environ.get("SCREENTIME_USER", os.getenv("USER", ""))
POLL_INTERVAL = 2.0
DESKTOP_SCAN_INTERVAL = 300

# Processes that are always allowed regardless of DB state
SYSTEM_PROCESS_NAMES = {
    # Init and session management
    "systemd", "systemd-journal", "systemd-logind", "systemd-udevd",
    "login", "sddm", "lightdm", "gdm",
    # KDE Plasma core
    "kwin_wayland", "kwin_x11", "plasmashell", "kded6", "kded5",
    "ksmserver", "kscreenlocker", "polkit-kde-authentication-agent-1",
    "kglobalaccel6", "baloo_file", "kaccess", "kwalletd6", "kwalletd5",
    "kactivitymanagerd", "kglobalaccel5", "plasma-vault", "kuiserver6",
    # D-Bus / IPC
    "dbus-broker", "dbus-daemon",
    # Audio / Video
    "pulseaudio", "pipewire", "pipewire-pulse", "wireplumber",
    # Display / Input
    "Xorg", "Xwayland",
    "fcitx5", "fcitx5-wayland", "fcitx", "ibus-daemon", "ibus-x11",
    "at-spi-bus-launcher", "at-spi2-registryd",
    # XDG portals
    "xdg-desktop-portal", "xdg-desktop-portal-kde", "xdg-desktop-portal-gtk",
    "xdg-permission-store", "xdg-document-portal",
    # Network / Bluetooth
    "NetworkManager", "wpa_supplicant", "bluetoothd", "avahi-daemon",
    # Screentime daemon itself
    "python3", "python", "uvicorn", "screentime", "main.py",
    # Shells (never kill shells, could break the session)
    "sh", "bash", "fish", "zsh", "dash",
    # Misc session helpers
    "gsd-xsettings", "gvfsd", "gvfsd-fuse",
    "mission-control-5", "gnome-keyring-daemon",
}
