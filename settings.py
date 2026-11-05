"""Where the plugin keeps things inside one Hermes home, and the small non-secret settings file the setup wizard,
the Desktop panel, the CLI and the provider share: $HERMES_HOME/agent-si/config.json with principal, brain_dir and
index_sessions. That is the flat JSON file Hermes reads and writes for a provider's dashboard panel
(config_schema.py declares STORAGE_FLAT_JSON), so a change made in Desktop is the change the provider sees.
0.1.0 kept the file at $HERMES_HOME/agent-si.json; load() moves it once. No secrets."""
from __future__ import annotations

import json
import os
import re
import tempfile
from pathlib import Path

PROVIDER = "agent-si"
BRAIN_NAME = "brain"
DEMO_NAME = "demo"
SETTINGS_NAME = "config.json"            # hermes_cli/web_routers/memory_providers._flat_json_path: home / name / config.json
LEGACY_SETTINGS_NAME = f"{PROVIDER}.json"  # 0.1.0
_TRUE = {"1", "true", "yes", "y", "on"}
_FALSE = {"0", "false", "no", "n", "off", ""}


def hermes_home(given: str | os.PathLike | None = None) -> Path:
    """The home a caller named, else $HERMES_HOME, else ~/.hermes. Providers bind to the one initialize() names."""
    return Path(given or os.environ.get("HERMES_HOME") or Path.home() / ".hermes").expanduser()


def settings_path(home: Path) -> Path:
    """$HERMES_HOME/agent-si/config.json: the file Hermes' dashboard panel reads and writes for this provider."""
    return data_dir(home) / SETTINGS_NAME


def legacy_settings_path(home: Path) -> Path:
    """Where 0.1.0 kept the settings. load() moves it to settings_path() once."""
    return Path(home) / LEGACY_SETTINGS_NAME


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
    if not path.exists():
        path = _adopt_legacy(home, path)
    try:
        data = json.loads(path.read_text(encoding="utf-8-sig"))
    except (OSError, ValueError):
        return {}
    return dict(data) if isinstance(data, dict) else {}


def _adopt_legacy(home: Path, path: Path) -> Path:
    """Move a 0.1.0 settings file to where the dashboard panel looks, once. Returns the file to read."""
    legacy = legacy_settings_path(home)
    if not legacy.is_file():
        return path
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        os.replace(legacy, path)
        return path
    except OSError:
        return legacy


def _stored(key: str, value):
    """The JSON the dashboard panel stores for this key: the bool field as true/false, text stripped."""
    if key == "index_sessions":
        return as_bool(value, True)
    return value.strip() if isinstance(value, str) else value


def save(home: Path, values: dict) -> dict:
    """Merge values into the settings file, atomically, owner-readable only, in the form the dashboard panel
    stores them. An empty value removes the key."""
    current = load(home)
    for key, value in dict(values or {}).items():
        if value is None or (isinstance(value, str) and not value.strip()):
            current.pop(key, None)
        else:
            current[key] = _stored(key, value)
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
