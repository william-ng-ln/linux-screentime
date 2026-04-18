import os
import pwd
import re
import shutil
import logging
from pathlib import Path
from configparser import RawConfigParser

from .database import Database, AppRecord
from . import config

log = logging.getLogger(__name__)

# Categories that indicate a user-facing application worth controlling.
# Apps must have at least ONE of these categories to be included.
USER_FACING_CATEGORIES = {
    "AudioVideo", "Audio", "Video",
    "Development", "IDE",
    "Education",
    "Game", "Games",
    "Graphics", "2DGraphics", "Photography", "VectorGraphics",
    "Office", "Spreadsheet", "Presentation", "WordProcessor",
    "Science",
    "Network",           # browsers, KDE Connect, chat — filter specific IDs below
    "WebBrowser",
    "InstantMessaging",
    "TextEditor",
    "TerminalEmulator",
    "FileManager",
    "Viewer", "Player",
    "Calculator",
    "Utility",           # broad but catches many user tools
    "X-WayDroid-App",   # Android apps via Waydroid
}

# Desktop file stems to always exclude even if they match a user category.
EXCLUDED_DESKTOP_IDS = {
    "bssh", "bvnc",                 # Avahi SSH/VNC server browsers
    "avahi-discover",               # Avahi Zeroconf Browser
    "jconsole-java-openjdk",        # Java developer console
    "jshell-java-openjdk",          # Java REPL
    "org.fcitx.Fcitx5",             # Input method daemon (not a user app)
    "fcitx5",                        # Same
    "kbd-layout-viewer5",           # Keyboard layout viewer (system tool)
    "org.kde.kdeconnect.nonplasma", # KDE Connect tray (duplicate of main entry)
}


def _get_desktop_dirs(username: str = "") -> list[Path]:
    dirs = []

    target = username or (config.TARGET_USER if os.getuid() == 0 else "")
    if target:
        try:
            user_home = Path(pwd.getpwnam(target).pw_dir)
        except KeyError:
            user_home = Path.home()
    else:
        user_home = Path.home()

    xdg_data_home = Path(os.environ.get("XDG_DATA_HOME", user_home / ".local/share"))
    dirs.append(xdg_data_home / "applications")

    xdg_data_dirs = os.environ.get(
        "XDG_DATA_DIRS",
        "/usr/local/share:/usr/share"
    )
    for d in xdg_data_dirs.split(":"):
        dirs.append(Path(d) / "applications")

    return [d for d in dirs if d.is_dir()]


def _is_shell_script(path: str) -> bool:
    try:
        with open(path, "rb") as f:
            return f.read(2) == b"#!"
    except OSError:
        return False


def _resolve_script_target(path: str, _depth: int = 5) -> str:
    """Follow exec chains in shell scripts to find the real ELF binary.

    Handles common patterns:
      exec /absolute/path ...
      exec -a NAME /absolute/path ...
      exec $WRAPPER "$sd_prog/binary.bin" ...   ($VAR/ replaced with script dir)
      exec $CHECK "$HERE/binary" ...            ($HERE replaced with script dir)
    """
    if _depth == 0:
        return ""
    script_dir = os.path.dirname(os.path.realpath(path))
    try:
        with open(path, "r", errors="replace") as f:
            content = f.read()
    except OSError:
        return ""

    for line in content.splitlines():
        line = line.strip()
        if not re.search(r'\bexec\b', line):
            continue

        # Replace $HERE / ${HERE} with the script's directory
        line = line.replace("$HERE", script_dir).replace("${HERE}", script_dir)
        # Replace any $VAR/ pattern with script_dir/ — covers $sd_prog/, $SNAP/, etc.
        # Most launcher scripts set such vars to their own directory.
        line = re.sub(r'\$\{?\w+\}?/', script_dir + '/', line)

        # Find the first absolute path after 'exec' (skip flags and wrapper vars)
        m = re.search(r'\bexec\b[^/]*(/[^\s"$\\]+)', line)
        if not m:
            continue

        candidate = m.group(1)
        if not os.path.isfile(candidate):
            continue

        if _is_shell_script(candidate):
            deeper = _resolve_script_target(candidate, _depth - 1)
            if deeper:
                return deeper
            # Script chain ended without finding ELF — return best we have
            return candidate
        else:
            return candidate  # Found real ELF binary

    return ""


def _parse_exec(exec_str: str) -> str:
    """Extract the real binary path from an Exec= value, following wrapper scripts."""
    # Remove field codes (%u, %F, etc.)
    cleaned = re.sub(r'%[a-zA-Z]', '', exec_str).strip()
    # Skip leading 'env' and FOO=bar assignments
    parts = cleaned.split()
    if not parts:
        return ""
    idx = 0
    while idx < len(parts) and (parts[idx] == "env" or "=" in parts[idx]):
        idx += 1
    if idx >= len(parts):
        return ""

    binary = parts[idx]
    resolved = shutil.which(binary) or binary
    resolved = os.path.realpath(resolved)  # follow symlinks

    # If it's a shell script wrapper, extract the real executable it calls
    if _is_shell_script(resolved):
        actual = _resolve_script_target(resolved)
        if actual:
            return os.path.realpath(actual)

    return resolved


def scan_desktop_files(db: Database, user_id: int = 1, username: str = "") -> int:
    """Scan all .desktop files and upsert into DB. Returns count of new/updated apps."""
    dirs = _get_desktop_dirs(username)
    seen: dict[str, AppRecord] = {}

    for d in dirs:
        for desktop_file in sorted(d.glob("*.desktop")):
            desktop_id = desktop_file.stem
            if desktop_id in seen:
                continue  # first (highest priority) dir wins

            try:
                app = _parse_desktop_file(desktop_file, desktop_id)
                if app:
                    app.user_id = user_id
                    seen[desktop_id] = app
            except Exception as e:
                log.debug("Skipping %s: %s", desktop_file, e)

    for app in seen.values():
        db.upsert_application(app)

    # Remove DB entries that no longer exist on disk
    db.remove_unlisted_apps(set(seen.keys()), user_id)

    count = len(seen)
    log.info("Desktop scan complete: %d applications found", count)
    return count


def _parse_desktop_file(path: Path, desktop_id: str) -> AppRecord | None:
    parser = RawConfigParser(strict=False)
    parser.read(str(path), encoding="utf-8")

    if not parser.has_section("Desktop Entry"):
        return None

    def get(key: str, fallback: str = "") -> str:
        return parser.get("Desktop Entry", key, fallback=fallback)

    entry_type = get("Type")
    if entry_type != "Application":
        return None

    if get("NoDisplay", "false").lower() == "true":
        return None
    if get("Hidden", "false").lower() == "true":
        return None

    # Skip known system utility apps by desktop ID
    if desktop_id in EXCLUDED_DESKTOP_IDS:
        return None

    name = get("Name")
    if not name:
        return None

    # Filter by categories: must have at least one user-facing category
    categories_str = get("Categories")
    cat_set = {c for c in categories_str.split(";") if c}
    if cat_set and not (cat_set & USER_FACING_CATEGORIES):
        log.debug("Skipping %s (no user-facing category): %s", desktop_id, categories_str)
        return None

    exec_val = get("Exec")
    if not exec_val:
        return None

    binary = _parse_exec(exec_val)
    if not binary:
        return None

    # For apps that share a binary (e.g. waydroid), store a unique arg for cmdline matching
    exec_args = ""
    if os.path.basename(binary) == "waydroid":
        m = re.search(r'waydroid\s+app\s+(?:launch|intent[^\s]*)\s+(\S+)', exec_val)
        if m:
            exec_args = m.group(1).strip("%")  # strip %u etc.

    return AppRecord(
        desktop_id=desktop_id,
        name=name,
        exec_binary=binary,
        icon=get("Icon"),
        categories=categories_str,
        allowed=False,
        daily_limit_minutes=0,
        exec_args=exec_args,
    )
