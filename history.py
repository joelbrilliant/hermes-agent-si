"""The history side: agent-memory's session index (vendored) over past Hermes, Claude Code, Codex and Grok sessions
on this machine. One SQLite file under the plugin's data folder, refreshed in the background after each turn,
searched with FTS5. Messages that look like secrets are never indexed (agent-memory's own rule)."""
from __future__ import annotations

import sqlite3
import threading
from pathlib import Path
from typing import Any

from . import _vendor  # noqa: F401  (vendor/ on sys.path)
import session_adapters
import session_continuity
from session_adapters import SessionDescriptor, SessionRoots

HUMAN_SOURCES = tuple(sorted(session_adapters.HUMAN_HERMES_SOURCES))


def discover_hermes_any(path: Path, *, strict: bool = False) -> list[SessionDescriptor]:
    """agent-memory's Hermes discovery, plus the sessions current Hermes writes with source 'local' and the real
    surface in created_source. Older state.db files have no created_source column and take the original query."""
    path = Path(path)
    if not path.is_file():
        if strict:
            raise FileNotFoundError(path)
        return []
    try:
        conn = sqlite3.connect(f"file:{path}?mode=ro", uri=True)
        try:
            columns = {row[1] for row in conn.execute("PRAGMA table_info(sessions)")}
            marks = ",".join("?" for _ in HUMAN_SOURCES)
            params: tuple = HUMAN_SOURCES
            if "created_source" in columns:
                where = f"(s.source IN ({marks}) OR (s.source = 'local' AND s.created_source IN ({marks})))"
                params = HUMAN_SOURCES + HUMAN_SOURCES
            else:
                where = f"s.source IN ({marks})"
            rows = conn.execute(
                f"""
                SELECT s.id, s.source, COALESCE(s.title, ''), COALESCE(s.cwd, ''), s.started_at,
                       COALESCE(MAX(m.timestamp), s.ended_at, s.started_at), COALESCE(MAX(m.id), 0), COUNT(m.id)
                FROM sessions s
                JOIN messages m ON m.session_id = s.id
                WHERE {where} AND m.role IN ('user', 'assistant')
                GROUP BY s.id
                """,
                params,
            ).fetchall()
        finally:
            conn.close()
    except sqlite3.Error:
        if strict:
            raise
        return []
    return [
        SessionDescriptor(
            harness="hermes", session_id=str(session_id), source_path=str(path),
            fingerprint=f"{max_message_id}:{message_count}", title=title or f"{source} session", cwd=cwd or "",
            started_at=float(started_at or 0), updated_at=float(updated_at or 0),
        )
        for session_id, source, title, cwd, started_at, updated_at, max_message_id, message_count in rows
    ]


# discover_sources() looks this name up at call time, so the index sees current Hermes too (vendor/VENDORED.md).
session_adapters.discover_hermes = discover_hermes_any


def roots_for(hermes_home: Path, *, claude: Path | None = None, codex: Path | None = None,
              grok: Path | None = None) -> SessionRoots:
    home = Path.home()
    return SessionRoots(
        hermes_db=Path(hermes_home) / "state.db",
        claude_root=claude or home / ".claude" / "projects",
        codex_root=codex or home / ".codex" / "sessions",
        grok_root=grok or home / ".grok" / "sessions",
    )


class History:
    def __init__(self, db_path: Path, roots: SessionRoots, *, enabled: bool = True):
        self.db = Path(db_path)
        self.roots = roots
        self.enabled = enabled
        self._lock = threading.Lock()

    def refresh(self) -> Any:
        """Index new and changed sessions. Blocking; the provider calls it from a background thread."""
        if not self.enabled:
            return None
        with self._lock:
            self.db.parent.mkdir(parents=True, exist_ok=True)
            return session_continuity.sync_index(self.db, self.roots)

    def search(self, query: str, *, limit: int = 10, current_session_id: str | None = None) -> list[Any]:
        """Ranked session hits from the cached index (no refresh on the read path), the live session left out."""
        if not self.enabled or not self.db.is_file() or not str(query or "").strip():
            return []
        try:
            return list(session_continuity.recall_sessions(
                self.db, query, limit=max(1, min(int(limit), 25)), sync=False, roots=self.roots,
                current_harness="hermes" if current_session_id else None, current_session_id=current_session_id or None,
            ))
        except (sqlite3.Error, OSError, ValueError):
            return []

    def read(self, harness: str, session_id: str, *, start: int = 0, limit: int = 40) -> dict:
        """One session's messages as a page, for brain_get, straight from the index's two tables."""
        if not self.db.is_file():
            return {"error": "no session index yet"}
        start = max(0, int(start))
        limit = max(1, min(int(limit), 200))
        try:
            conn = session_continuity.connect_readonly(self.db)
        except Exception as exc:
            return {"error": f"the session index could not be opened: {type(exc).__name__}"}
        try:
            row = conn.execute(
                "SELECT title, cwd, started_at, updated_at FROM session_sources WHERE harness = ? AND session_id = ?",
                (harness, session_id)).fetchone()
            if row is None:
                return {"error": f"no session {harness}:{session_id} in the index"}
            total = conn.execute(
                "SELECT count(*) FROM session_messages WHERE harness = ? AND session_id = ?",
                (harness, session_id)).fetchone()[0]
            rows = conn.execute(
                "SELECT role, content, timestamp FROM session_messages WHERE harness = ? AND session_id = ? "
                "ORDER BY timestamp, id LIMIT ? OFFSET ?", (harness, session_id, limit, start)).fetchall()
        except sqlite3.Error as exc:
            return {"error": f"session could not be read: {type(exc).__name__}"}
        finally:
            conn.close()
        shown = start + len(rows)
        return {
            "side": "history",
            "id": f"{harness}:{session_id}",
            "harness": harness,
            "title": row[0],
            "cwd": row[1],
            "started_at": row[2],
            "updated_at": row[3],
            "message_count": total,
            "messages": [{"role": role, "at": at, "text": text} for role, text, at in rows],
            "next_start": shown if shown < total else None,
        }

    def counts(self) -> dict[str, int]:
        if not self.db.is_file():
            return {}
        try:
            conn = session_continuity.connect_readonly(self.db)
        except Exception:
            return {}
        try:
            return {row[0]: row[1] for row in conn.execute(
                "SELECT harness, count(*) FROM session_sources GROUP BY harness ORDER BY harness")}
        except sqlite3.Error:
            return {}
        finally:
            conn.close()
