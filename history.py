"""The history side: agent-memory's session index (vendored) over past Hermes, Claude Code, Codex and Grok sessions
on this machine. One SQLite file under the plugin's data folder, refreshed in the background after each turn,
searched with FTS5. Messages that look like secrets are never indexed (agent-memory's own rule)."""
from __future__ import annotations

import sqlite3
import threading
from pathlib import Path
from types import SimpleNamespace
from typing import Any

from . import _vendor  # noqa: F401  (vendor/ on sys.path)
from .fuse import clock_time  # stored seconds as local clock time, so the reader never converts by hand
import session_continuity
from session_adapters import SessionRoots

PERIOD_EXCERPT_CHARS = 600


def roots_for(hermes_home: Path, *, claude: Path | None = None, codex: Path | None = None,
              grok: Path | None = None) -> SessionRoots:
    home = Path.home()
    return SessionRoots(
        hermes_db=Path(hermes_home) / "state.db",
        claude_root=claude or home / ".claude" / "projects",
        codex_root=codex or home / ".codex" / "sessions",
        grok_root=grok or home / ".grok" / "sessions",
    )


def day_bounds(since: str = "", until: str = "") -> tuple[float | None, float | None]:
    """Local YYYY-MM-DD days as (the first second of since, the first second after until), so both days are
    included. An empty day is no bound. Raises ValueError for anything that is not such a date."""
    return (session_continuity.day_start(since) if since else None,
            session_continuity.day_end(until) if until else None)


def _is_live(harness: str, session_id: str, current_session_id: str | None) -> bool:
    if not current_session_id or harness != "hermes":
        return False
    return session_id == current_session_id or session_id.partition(":")[2] == current_session_id


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

    def search(self, query: str, *, limit: int = 10, current_session_id: str | None = None,
               all_terms_first: bool = False, since: float | None = None, before: float | None = None) -> list[Any]:
        """Ranked session hits from the cached index (no refresh on the read path), the live session left out.
        A search ranks by any word. all_terms_first, for the recall nobody asked for, keeps messages holding every
        word and broadens only when none do. since and before keep messages inside those times."""
        if not self.enabled or not self.db.is_file() or not str(query or "").strip():
            return []
        try:
            return list(session_continuity.recall_sessions(
                self.db, query, limit=max(1, min(int(limit), 25)), sync=False, roots=self.roots,
                current_harness="hermes" if current_session_id else None, current_session_id=current_session_id or None,
                all_terms_first=all_terms_first, since=since, before=before,
            ))
        except (sqlite3.Error, OSError, ValueError):
            return []

    def period(self, since: float | None, before: float | None, *, limit: int = 10,
               current_session_id: str | None = None) -> list[Any]:
        """The sessions with a message inside the times, newest first, each with its first message there as the
        excerpt. For a dated question whose words match nothing ("what did we talk about yesterday?")."""
        if not self.enabled or not self.db.is_file() or (since is None and before is None):
            return []
        where, params = ["m.timestamp > 0"], []
        if since is not None:
            where.append("m.timestamp >= ?")
            params.append(since)
        if before is not None:
            where.append("m.timestamp < ?")
            params.append(before)
        limit = max(1, min(int(limit), 25))
        try:
            conn = session_continuity.connect_readonly(self.db)
        except Exception:
            return []
        try:
            rows = conn.execute(
                "SELECT s.harness, s.session_id, s.title, s.cwd, s.started_at, MAX(m.timestamp), MIN(m.id) "
                "FROM session_messages m JOIN session_sources s ON s.harness = m.harness AND s.session_id = m.session_id "
                "WHERE " + " AND ".join(where) + " GROUP BY s.harness, s.session_id ORDER BY MAX(m.timestamp) DESC LIMIT ?",
                (*params, limit + 1)).fetchall()
            hits = []
            for harness, session_id, title, cwd, started_at, last_at, first_id in rows:
                if _is_live(harness, session_id, current_session_id):
                    continue
                role, text, at = conn.execute(
                    "SELECT role, content, timestamp FROM session_messages WHERE id = ?", (first_id,)).fetchone()
                position = conn.execute(
                    "SELECT COUNT(*) + 1 FROM session_messages WHERE harness = ? AND session_id = ? AND id < ?",
                    (harness, session_id, first_id)).fetchone()[0]
                hits.append(SimpleNamespace(
                    harness=harness, session_id=session_id, title=title or "", cwd=cwd or "",
                    started_at=float(started_at or 0), updated_at=float(last_at or 0),
                    messages=(SimpleNamespace(role=role, text=str(text or "")[:PERIOD_EXCERPT_CHARS],
                                              timestamp=float(at or 0), position=int(position)),),
                ))
            return hits[:limit]
        except sqlite3.Error:
            return []
        finally:
            conn.close()

    def read(self, harness: str, session_id: str, *, start: int = 0, limit: int = 40) -> dict:
        """One session's messages as a page, for brain_get. start counts messages already passed (0 is the first)."""
        if not self.db.is_file():
            return {"error": "no session index yet"}
        try:
            page = session_continuity.read_session(self.db, session_id, harness=harness,
                                                   start=max(0, int(start)) + 1, limit=max(1, min(int(limit), 200)))
        except (sqlite3.Error, OSError, ValueError) as exc:
            return {"error": f"session could not be read: {type(exc).__name__}"}
        if page is None:
            return {"error": f"no session {harness}:{session_id} in the index"}
        session = page.session
        return {
            "side": "history",
            "id": f"{session.harness}:{session.session_id}",
            "harness": session.harness,
            "title": session.title,
            "cwd": session.cwd,
            "started_at": clock_time(session.started_at),
            "updated_at": clock_time(session.updated_at),
            "message_count": session.message_count,
            "messages": [{"n": message.position, "role": message.role, "at": clock_time(message.timestamp),
                          "text": message.text} for message in page.messages],
            "next_start": page.next_start - 1 if page.next_start else None,
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
