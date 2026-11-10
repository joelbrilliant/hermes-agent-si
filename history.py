"""The history side: the engine's own session record (brain.sessions, vendored). One SQLite file beside the brain
holds what was said in past Hermes, Claude Code, Codex and Grok sessions on this machine, word for word, added by
code and never rewritten. The plugin refreshes it in a background thread, with the sessions of the Hermes home it
was started for, and reads it with the engine's functions in the engine's shapes, so a past session reads here as
it does from the engine's own search. Messages that look like secrets are never recorded (the engine's rule)."""
from __future__ import annotations

import sqlite3
import threading
from dataclasses import replace
from pathlib import Path
from typing import Any, Mapping

from . import _vendor  # noqa: F401  (vendor/ on sys.path)
from brain.sessions import record
from brain.sessions.adapters import SessionRoots
from brain.text import ELLIPSIS, single_line

# How many sessions a search shows and how much of each, as the engine's own search does it (brain.reads, 1.16.1).
# Beside brain hits they are leads: a few, each with one short line, because every search pays for them. When the
# brain has no hit, or only sessions were asked for, the sessions are the answer and come deeper.
SESSIONS_BESIDE = 3
SESSIONS_ALONE = 5
LEAD_EXCERPTS = 1
LEAD_CHARS = 160
TITLE_CHARS = 80
PERIOD_NOTE = "No session in those dates had the words; these are that period's sessions, newest first."


def roots_for(hermes_home: Path, *, profiles: Mapping[str, str] | None = None, claude: Path | None = None,
              codex: Path | None = None, grok: Path | None = None) -> SessionRoots:
    """Where the sessions come from: the tools' default folders, Hermes sessions from the Hermes home the provider
    was started for (whatever $HERMES_HOME says in this process), and any other Hermes profiles the brain's
    brain.toml names under [sessions]."""
    roots = SessionRoots.default(hermes_profiles=profiles)
    return replace(roots, hermes_db=Path(hermes_home) / "state.db", claude_root=claude or roots.claude_root,
                   codex_root=codex or roots.codex_root, grok_root=grok or roots.grok_root)


def record_path(brain_dir: Path) -> Path:
    """The record's file: sessions.sqlite3 in the brain's own folder, where the engine keeps it."""
    return record.path_for(brain_dir)


def valid_days(since: str = "", until: str = "") -> bool:
    """Whether both are empty or local days written YYYY-MM-DD."""
    try:
        record.day_bounds(since or None, until or None, "UTC")
    except ValueError:
        return False
    return True


def item(hit: Any, zone: str) -> dict[str, Any]:
    """One session as the engine's search shows it. The id ends in the first excerpt's message number, so opening
    it lands on that line and not at the session's start."""
    excerpts = []
    for excerpt in hit.excerpts:
        entry = {"n": excerpt.n, "role": excerpt.role, "text": excerpt.text}
        at = record.clock(excerpt.timestamp, zone)
        if at != record.NO_TIME:
            entry["at"] = at
        excerpts.append(entry)
    place = record.place_ref(hit.harness, hit.session_id, hit.excerpts[0].n) if hit.excerpts else hit.id
    return {
        "id": place,
        "title": single_line(hit.title, max_chars=TITLE_CHARS, ellipsis=ELLIPSIS),
        "date": record.local_day(hit.updated_at, zone) or None,
        "excerpts": excerpts,
    }


class History:
    def __init__(self, db_path: Path, roots: SessionRoots, *, zone: str = "UTC", enabled: bool = True):
        self.db = Path(db_path)
        self.roots = roots
        self.zone = zone
        self.enabled = enabled
        self._lock = threading.Lock()

    @staticmethod
    def is_session(ident: str) -> bool:
        """Whether an id names a past session (hermes:…, claude:…, codex:…, grok:…) and not a row of the brain."""
        return record.parse_place(ident) is not None

    def refresh(self, *, wait: bool = True) -> Any:
        """Add what the tools' session stores hold and the record lacks. Blocking; the provider calls it from a
        background thread. With wait False, a refresh already running here is left to finish and this one does
        nothing. Another process refreshing the same record is never waited for (the engine's own lock)."""
        if not self.enabled or not self._lock.acquire(blocking=wait):
            return None
        try:
            self.db.parent.mkdir(parents=True, exist_ok=True)
            return record.sync(self.db, self.roots)
        finally:
            self._lock.release()

    def find(self, query: str, *, limit: int, deep: bool, since: str = "", until: str = "",
             asking: tuple[str, str] | None = None, all_terms_first: bool = False) -> tuple[list[dict], str]:
        """The sessions for a search, as the engine's search chooses them, and a line to pass on when the dates
        alone chose them. A search ranks by any word; all_terms_first, for the recall nobody asked for, keeps
        messages holding every word and widens only when none do. since and until are local YYYY-MM-DD days, both
        included; another shape raises ValueError. asking is the live session, left out. No refresh here."""
        first, after = record.day_bounds(since or None, until or None, self.zone)
        if not self.enabled or not self.db.is_file():
            return [], ""
        excerpts = record.MAX_EXCERPTS if deep else LEAD_EXCERPTS
        chars = record.EXCERPT_CHARS if deep else LEAD_CHARS
        limit = max(1, min(int(limit), 25))
        worded = bool(record.fts_terms(query))
        found: list = []
        note = ""
        try:
            if worded:
                found = record.search(self.db, query, limit=limit, excerpts=excerpts, excerpt_chars=chars,
                                      exclude=asking, since=first, before=after, all_terms_first=all_terms_first)
            if not found and (since or until):
                found = record.period(self.db, first, after, limit=limit, exclude=asking, excerpt_chars=chars)
                if found and worded:
                    note = PERIOD_NOTE
        except (sqlite3.Error, OSError):
            return [], "The session record couldn't be read; past sessions are left out of this answer."
        return [item(hit, self.zone) for hit in found], note

    def read(self, ident: str, offset: int = 0) -> dict:
        """One page of a past session's text, in the shape the engine's get gives a session. An id that ends in a
        message number (#n, as a search shows it) opens at that message; offset, when given, is where the page
        starts, and next_offset continues it."""
        place = record.parse_place(ident)
        if place is None:
            return {"error": f"{ident!r} is not a session id; one reads like hermes:<session id> or hermes:<session id>#12"}
        harness, session_id, at = place
        ref = record.session_ref(harness, session_id)
        try:
            page = record.read(self.db, harness, session_id, self.zone, offset=max(0, int(offset)), at=at)
        except record.NoSuchMessage as missing:
            return {"error": f"{ref} has no message {missing.n}; it holds {missing.messages}. Open {ref} from its "
                             "start, or use a number a search showed."}
        except (sqlite3.Error, OSError) as exc:
            return {"error": f"the session could not be read: {type(exc).__name__}"}
        if page is None:
            return {"error": f"no session {ref} in the record"}
        row = {
            "id": ref,
            "tool": page["harness"],
            "title": single_line(page["title"], max_chars=TITLE_CHARS, ellipsis=ELLIPSIS),
            "cwd": page["cwd"],
            "started_at": record.clock(page["started_at"], self.zone),
            "updated_at": record.clock(page["updated_at"], self.zone),
            "messages": page["messages"],
            "content": page["text"],
        }
        pages = {"content": {"offset": page["offset"], "returned": page["returned"], "total": page["chars"],
                             "next_offset": page["next_offset"]}}
        return {"entity": "session", "id": ref, "row": row, "pages": pages, "truncated": False}

    def forget(self, ident: str) -> dict:
        """Remove one session's words from the record and keep it out of later refreshes, whether or not its tool
        still has it. The engine's own `brain sessions forget`."""
        place = record.parse_place(ident)
        if place is None:
            return {"error": f"{ident!r} is not a session id; one reads like hermes:<session id>"}
        harness, session_id, _ = place
        if not self.db.is_file():
            return {"error": "there is no record of past sessions here yet"}
        with self._lock:
            removed = record.forget(self.db, harness, session_id)
        return {"status": "forgotten", "session": record.session_ref(harness, session_id), "messages_removed": removed}

    def status(self) -> dict:
        """What the record holds, in counts only: sessions per tool, messages, the last refresh."""
        try:
            return record.status(self.db)
        except (sqlite3.Error, OSError):
            return {"exists": False, "sessions": {}, "messages": 0}
