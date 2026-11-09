"""Test fixtures: load this plugin directory as a package the way Hermes does (a package whose __path__ is the
directory, so relative imports work), with Hermes' own code on sys.path for `agent.memory_provider`.

Hermes is found at $HERMES_AGENT_SRC, else ~/.hermes/hermes-agent. Tests skip when it is absent. Tests reach the
helpers through fixtures only (pytest runs in importlib mode, so conftest is not importable by name)."""
from __future__ import annotations

import importlib
import importlib.util
import os
import sqlite3
import sys
import time
from pathlib import Path

import pytest

PLUGIN_DIR = Path(__file__).resolve().parents[1]
PACKAGE = "agent_si_plugin_under_test"
HERMES_SRC = Path(os.environ.get("HERMES_AGENT_SRC") or Path.home() / ".hermes" / "hermes-agent")


def _hermes_available() -> bool:
    return (HERMES_SRC / "agent" / "memory_provider.py").is_file()


def _load_plugin():
    if PACKAGE in sys.modules:
        return sys.modules[PACKAGE]
    if str(HERMES_SRC) not in sys.path:
        sys.path.insert(0, str(HERMES_SRC))
    spec = importlib.util.spec_from_file_location(PACKAGE, PLUGIN_DIR / "__init__.py",
                                                  submodule_search_locations=[str(PLUGIN_DIR)])
    module = importlib.util.module_from_spec(spec)
    sys.modules[PACKAGE] = module
    spec.loader.exec_module(module)
    return module


@pytest.fixture(scope="session")
def plugin():
    """The plugin package, imported once per test session."""
    if not _hermes_available():
        pytest.skip(f"Hermes source not found at {HERMES_SRC}; set HERMES_AGENT_SRC")
    return _load_plugin()


@pytest.fixture(scope="session")
def plugin_module(plugin):
    """A sibling module of the plugin (fuse, remember, settings, history, brainside), through the package."""
    return lambda name: importlib.import_module(f"{PACKAGE}.{name}")


@pytest.fixture(scope="session")
def plugin_dir() -> Path:
    return PLUGIN_DIR


@pytest.fixture
def home(tmp_path, monkeypatch):
    """A fresh Hermes home."""
    hermes_home = tmp_path / "hermes-home"
    hermes_home.mkdir()
    monkeypatch.setenv("HERMES_HOME", str(hermes_home))
    return hermes_home


@pytest.fixture
def make_state_db():
    """Writes a Hermes state.db with the two tables the index reads. current_schema adds created_source and writes
    source='local' rows the way current Hermes does; otherwise the older layout with the surface in source. A session
    may give its own 'started' time (seconds); its messages follow a minute apart."""
    def make(path: Path, *, current_schema: bool, sessions: list[dict]) -> None:
        conn = sqlite3.connect(path)
        created = ", created_source TEXT" if current_schema else ""
        conn.executescript(f"""
            CREATE TABLE sessions (id TEXT PRIMARY KEY, source TEXT NOT NULL{created}, title TEXT, cwd TEXT,
                                   started_at REAL NOT NULL, ended_at REAL);
            CREATE TABLE messages (id INTEGER PRIMARY KEY AUTOINCREMENT, session_id TEXT NOT NULL,
                                   role TEXT NOT NULL, content TEXT, timestamp REAL NOT NULL);
        """)
        now = time.time()
        for n, session in enumerate(sessions):
            surface = session.get("surface", "cli")
            started = session.get("started", now - 3600 * (n + 1))
            if current_schema:
                conn.execute("INSERT INTO sessions VALUES (?,?,?,?,?,?,?)",
                             (session["id"], "local", surface, session.get("title", ""), session.get("cwd", ""),
                              started, None))
            else:
                conn.execute("INSERT INTO sessions VALUES (?,?,?,?,?,?)",
                             (session["id"], surface, session.get("title", ""), session.get("cwd", ""), started, None))
            for k, (role, text) in enumerate(session["messages"]):
                conn.execute("INSERT INTO messages (session_id, role, content, timestamp) VALUES (?,?,?,?)",
                             (session["id"], role, text, started + 60 * k))
        conn.commit()
        conn.close()
    return make
