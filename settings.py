"""Where the plugin keeps things inside one Hermes home, and the small non-secret settings file the setup wizard
and the dashboard write: $HERMES_HOME/agent-si.json with principal, brain_dir and index_sessions. No secrets."""
from __future__ import annotations

import json
import os
import re
import tempfile
from pathlib import Path

PROVIDER = "agent-si"
BRAIN_NAME = "brain"
DEMO_NAME = "demo"
_TRUE = {"1", "true", "yes", "y", "on"}
_FALSE = {"0", "false", "no", "n", "off", ""}


def hermes_home(given: str | os.PathLike | None = None) -> Path:
    """The home a caller named, else $HERMES_HOME, else ~/.hermes. Providers bind to the one initialize() names."""
    return Path(given or os.environ.get("HERMES_HOME") or Path.home() / ".hermes").expanduser()


def settings_path(home: Path) -> Path:
    return Path(home) / f"{PROVIDER}.json"


def data_dir(home: Path) -> Path:
    return Path(home) / PROVIDER


def default_brain_dir(home: Path) -> Path:
    return data_dir(home) / BRAIN_NAME


def demo_dir(home: Path) -> Path:
    return data_dir(home) / DEMO_NAME


def sessions_db(home: Path) -> Path:
    return data_dir(home) / "sessions.db"


def load(home: Path) -> dict:
    path = settings_path(home)
    try:
        data = json.loads(path.read_text(encoding="utf-8-sig"))
    except (OSError, ValueError):
        return {}
    return dict(data) if isinstance(data, dict) else {}


def save(home: Path, values: dict) -> dict:
    """Merge values into the settings file, atomically, owner-readable only. An empty value removes the key."""
    current = load(home)
    for key, value in dict(values or {}).items():
        if value is None or (isinstance(value, str) and not value.strip()):
            current.pop(key, None)
        else:
            current[key] = value.strip() if isinstance(value, str) else value
    path = settings_path(home)
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(prefix=".agent-si.", suffix=".json", dir=str(path.parent))
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            json.dump(current, handle, indent=2, ensure_ascii=False)
            handle.write("\n")
        os.chmod(tmp, 0o600)
        os.replace(tmp, path)
    except BaseException:
        try:
            os.unlink(tmp)
        except OSError:
            pass
        raise
    return current


def as_bool(value, default: bool = True) -> bool:
    if isinstance(value, bool):
        return value
    if value is None:
        return default
    text = str(value).strip().casefold()
    if text in _TRUE:
        return True
    if text in _FALSE:
        return False
    return default


def brain_dir(home: Path, values: dict | None = None) -> tuple[Path, bool]:
    """(folder, chosen_by_user): the brain the settings name, else the default one in this home."""
    values = load(home) if values is None else values
    given = str(values.get("brain_dir") or "").strip()
    if given:
        return Path(given).expanduser(), True
    return default_brain_dir(home), False


def local_timezone() -> str:
    """An IANA zone for a new brain: $TZ when it is one, else the zone /etc/localtime points at, else UTC."""
    from zoneinfo import ZoneInfo

    candidates = [os.environ.get("TZ") or ""]
    try:
        target = os.readlink("/etc/localtime")
        match = re.search(r"zoneinfo/(.+)$", target)
        if match:
            candidates.append(match.group(1))
    except OSError:
        pass
    for name in candidates:
        if name and "/" in name or name == "UTC":
            try:
                ZoneInfo(name)
                return name
            except Exception:
                continue
    return "UTC"
