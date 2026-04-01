# linux-screentime

A parental control application for Linux (Arch Linux / KDE Plasma 6) that monitors and enforces screen time limits for child user accounts.

![App Icon](screentime.svg)

## Features

- **Application allowlist** — only permitted apps can run; blocked apps are killed immediately with a fullscreen notification
- **Daily time limits** — set per-app time limits in minutes per day
- **Per-day-of-week schedule** — different limits for each day (e.g. weekdays vs. weekends)
- **Usage history** — charts showing daily and hourly usage per app
- **5-minute warning** — `notify-send` alert before time runs out (shows the app's own icon)
- **Fullscreen overlay** — blocks the screen with a countdown when an app is killed or time expires
- **Waydroid support** — tracks and kills individual Android apps running inside Waydroid
- **Secure by design** — database is root-only; admin GUI talks to the daemon via an authenticated Unix socket

## Architecture

The app runs as two separate processes:

| Process | Runs as | Description |
|---|---|---|
| `daemon.py` | `root` (systemd system service) | Monitors processes, enforces limits, manages the database |
| `main.py` | Parent user (systemd user service) | Qt admin GUI + system tray |

The admin GUI never touches the database directly. It communicates with the daemon over a Unix domain socket (`/run/screentime/control.sock`) using a JSON protocol with token-based authentication.

### Security model

- Database (`/var/lib/screentime/screentime.db`): `root:root 600` — child cannot read, write, or delete it
- IPC socket: `chmod 0666` — anyone can connect, but **write commands require a valid token**
- Token is obtained by calling `authenticate(password)` — 64-char random hex, 8-hour sliding TTL
- `set_password` revokes all active tokens
- Closing the admin window locks it and discards the token from memory

## Requirements

- Arch Linux (or similar systemd-based distro)
- KDE Plasma 6 / Wayland or X11
- `python-pyqt6` (system package: `sudo pacman -S python-pyqt6`)
- `psutil` (installed automatically into a venv by `install.sh`)
- `libnotify` for `notify-send` warnings

## Installation

```bash
# Clone the repo
git clone https://github.com/your-username/linux-screentime.git
cd linux-screentime

# Install (requires root)
sudo ./install.sh
```

The installer will:
1. Copy files to `/opt/screentime/`
2. Create a Python venv at `/opt/screentime/.venv`
3. Set up the database directory with correct permissions
4. Register and start `screentime-daemon.service` (system)
5. Register and start `screentime.service` (user, autostart)
6. Install the app icon to the system icon theme
7. Install the `.desktop` file

## First-time setup

1. Open the admin panel from the system tray icon
2. Log in with the default password: **`admin`**
3. Go to **Settings** → enter the child's Linux username → Save
4. Go to **Applications** → allow the apps the child should be able to use
5. Back in **Settings** → enable **Application control**

> Change the password immediately in Settings → Change Password.

## Usage

### Admin GUI

Launch from the system tray or application menu. The window starts on the login screen and locks again when closed.

**Applications tab** — toggle the allowlist checkbox for each app. Click the schedule button in the last column to set time limits:
- *No limit* — app runs freely
- *Every day* — one daily limit in minutes
- *Per day of week* — separate limits for Mon–Sun

**History tab** — bar charts of daily usage per app. Double-click a bar for an hourly breakdown.

**Settings tab** — enable/disable enforcement, set the child's username, change the admin password.

### Systemd services

```bash
# Daemon (root)
sudo systemctl status screentime-daemon
sudo journalctl -u screentime-daemon -f

# Admin GUI (parent user)
systemctl --user status screentime
journalctl --user -u screentime -f
```

## Project structure

```
daemon.py                    # Root daemon entry point
main.py                      # Qt admin GUI entry point
install.sh                   # Installer script
screentime.svg               # App icon
screentime-daemon.service    # systemd system service
screentime.service           # systemd user service
screentime-admin.desktop     # .desktop file

screentime/
  config.py                  # Constants: TARGET_USER, DB_PATH, POLL_INTERVAL
  database.py                # SQLite wrapper, AppRecord dataclass
  enforcer.py                # Process monitor + kill thread
  time_tracker.py            # Per-app session time tracking
  desktop_scanner.py         # .desktop file parser, shell script resolver
  notifier.py                # Sends overlay / notify-send to child's screen
  overlay.py                 # Fullscreen block notification (runs as child user)
  ipc.py                     # IPC constants: socket path, token TTL, command sets
  ipc_server.py              # Unix socket server thread (inside daemon)
  ipc_client.py              # AdminClient: drop-in replacement for Database
  ui/
    admin_window.py          # AdminWindow, ScheduleDialog, AppsTab, HistoryTab
    tray.py                  # System tray icon
```

## How it works

1. The daemon polls every 2 seconds, listing all processes owned by the child user.
2. Each process is matched to an app entry using a 5-step lookup (exact path → realpath → basename → stem → cmdline).
3. If the app is not on the allowlist → it is killed and a fullscreen overlay is shown.
4. If the app has a time limit → accumulated usage is checked. A warning is sent at 5 minutes remaining; the app is killed when time runs out.
5. All events are recorded in the SQLite database for history display.

## Known limitations / possible improvements

- Time limits are per-app, not a shared daily total across all apps
- No way for the child to request extra time (parent approval flow)
- Tested on Arch Linux / KDE Plasma 6; other distros may need adjustments

## License

MIT
