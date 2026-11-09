#!/usr/bin/env python3
"""Deterministic cross-harness session index and recall."""

from __future__ import annotations

import argparse
import os
import re
import sqlite3
import sys
import time
from collections import Counter
from dataclasses import dataclass
from datetime import datetime, timedelta
from pathlib import Path

from ingest import has_secret
from db_permissions import (
    enforce_private_database,
    prepare_private_database,
)
from session_adapters import (
    GENERATED_MESSAGE_PREFIXES,
    SessionDescriptor,
    SessionRoots,
    clean_message_content,
    discover_all,
    discover_sources,
    load_messages,
)


SCRIPT_DIR = Path(__file__).resolve().parent
DEFAULT_DB_PATH = SCRIPT_DIR / "sessions.db"
USAGE_LOG_PATH = Path(
    os.environ.get("AGENT_MEMORY_USAGE_LOG", str(SCRIPT_DIR / "usage.log"))
)
MAX_MESSAGE_CHARS = 32_000
MAX_EXCERPTS_PER_SESSION = 3
MAX_READ_MESSAGES = 200
MAX_READ_CHARS = 40_000
INDEX_FORMAT_VERSION = "session-v2"
SESSION_SECRET_ASSIGNMENT = re.compile(
    r"(?:^|\s)(?:export\s+)?"
    r"[A-Z0-9_]*(?:API_KEY|TOKEN|SECRET|PASSWORD)"
    r"\s*=\s*\S{16,}",
    re.IGNORECASE | re.MULTILINE,
)


@dataclass(frozen=True)
class SessionExcerpt:
    role: str
    timestamp: float
    text: str
    position: int = 0


@dataclass(frozen=True)
class SessionHit:
    harness: str
    session_id: str
    title: str
    cwd: str
    started_at: float
    updated_at: float
    source_path: str
    score: float
    messages: tuple[SessionExcerpt, ...]
    source_status: str = "cached"


@dataclass(frozen=True)
class SessionSummary:
    harness: str
    session_id: str
    title: str
    cwd: str
    started_at: float
    updated_at: float
    source_path: str
    message_count: int


@dataclass(frozen=True)
class SessionPage:
    session: SessionSummary
    messages: tuple[SessionExcerpt, ...]
    next_start: int | None


@dataclass(frozen=True)
class SessionSyncResult:
    sessions_by_harness: dict[str, int]
    changed_sessions: int
    indexed_messages: int
    skipped_secret: int
    elapsed_seconds: float
    source_errors: tuple[dict, ...] = ()


def connect(db_path: Path) -> sqlite3.Connection:
    db_path = prepare_private_database(db_path)
    conn = sqlite3.connect(db_path, timeout=2.0)
    conn.execute("PRAGMA busy_timeout=2000")
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA foreign_keys=ON")
    ensure_schema(conn)
    enforce_private_database(db_path)
    return conn


def connect_readonly(db_path: Path) -> sqlite3.Connection:
    enforce_private_database(db_path)
    uri = db_path.resolve().as_uri() + "?mode=ro"
    conn = sqlite3.connect(uri, uri=True, timeout=2.0)
    conn.execute("PRAGMA busy_timeout=2000")
    conn.execute("PRAGMA query_only=ON")
    return conn


def ensure_schema(conn: sqlite3.Connection) -> None:
    conn.executescript(
        """
        CREATE TABLE IF NOT EXISTS session_sources (
            harness TEXT NOT NULL,
            session_id TEXT NOT NULL,
            source_path TEXT NOT NULL,
            fingerprint TEXT NOT NULL,
            title TEXT NOT NULL DEFAULT '',
            cwd TEXT NOT NULL DEFAULT '',
            started_at REAL NOT NULL DEFAULT 0,
            updated_at REAL NOT NULL DEFAULT 0,
            PRIMARY KEY (harness, session_id)
        );

        CREATE TABLE IF NOT EXISTS session_messages (
            id INTEGER PRIMARY KEY,
            harness TEXT NOT NULL,
            session_id TEXT NOT NULL,
            message_key TEXT NOT NULL,
            role TEXT NOT NULL,
            content TEXT NOT NULL,
            timestamp REAL NOT NULL DEFAULT 0,
            UNIQUE (harness, session_id, message_key),
            FOREIGN KEY (harness, session_id)
                REFERENCES session_sources(harness, session_id)
                ON DELETE CASCADE
        );

        CREATE VIRTUAL TABLE IF NOT EXISTS session_messages_fts USING fts5(
            content,
            content='session_messages',
            content_rowid='id',
            tokenize='porter unicode61'
        );

        CREATE TRIGGER IF NOT EXISTS session_messages_insert
        AFTER INSERT ON session_messages BEGIN
            INSERT INTO session_messages_fts(rowid, content)
            VALUES (new.id, new.content);
        END;

        CREATE TRIGGER IF NOT EXISTS session_messages_delete
        AFTER DELETE ON session_messages BEGIN
            INSERT INTO session_messages_fts(
                session_messages_fts,
                rowid,
                content
            ) VALUES ('delete', old.id, old.content);
        END;

        CREATE INDEX IF NOT EXISTS session_messages_session
        ON session_messages(harness, session_id, timestamp);

        CREATE INDEX IF NOT EXISTS session_sources_updated
        ON session_sources(updated_at DESC);
        """
    )


def safe_content(content: str, role: str | None = None) -> str | None:
    content = content.replace("\x00", "").strip()
    if role:
        content = clean_message_content(role, content)
    if (
        not content
        or content.startswith(GENERATED_MESSAGE_PREFIXES)
        or has_secret(content)
        or SESSION_SECRET_ASSIGNMENT.search(content)
    ):
        return None
    return content[:MAX_MESSAGE_CHARS]


def _delete_session(
    conn: sqlite3.Connection,
    harness: str,
    session_id: str,
) -> None:
    conn.execute(
        "DELETE FROM session_messages WHERE harness = ? AND session_id = ?",
        (harness, session_id),
    )
    conn.execute(
        "DELETE FROM session_sources WHERE harness = ? AND session_id = ?",
        (harness, session_id),
    )


def _replace_session(
    conn: sqlite3.Connection,
    descriptor: SessionDescriptor,
    indexed_fingerprint: str,
) -> tuple[int, int]:
    _delete_session(conn, descriptor.harness, descriptor.session_id)
    conn.execute(
        """
        INSERT INTO session_sources(
            harness,
            session_id,
            source_path,
            fingerprint,
            title,
            cwd,
            started_at,
            updated_at
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            descriptor.harness,
            descriptor.session_id,
            descriptor.source_path,
            indexed_fingerprint,
            descriptor.title,
            descriptor.cwd,
            descriptor.started_at,
            descriptor.updated_at,
        ),
    )
    indexed = 0
    skipped_secret = 0
    for message in load_messages(descriptor):
        content = safe_content(message.content, role=message.role)
        if content is None:
            if message.content.strip() and (
                has_secret(message.content)
                or SESSION_SECRET_ASSIGNMENT.search(message.content)
            ):
                skipped_secret += 1
            continue
        cursor = conn.execute(
            """
            INSERT OR IGNORE INTO session_messages(
                harness,
                session_id,
                message_key,
                role,
                content,
                timestamp
            ) VALUES (?, ?, ?, ?, ?, ?)
            """,
            (
                descriptor.harness,
                descriptor.session_id,
                message.message_key,
                message.role,
                content,
                message.timestamp,
            ),
        )
        indexed += max(0, cursor.rowcount)
    return indexed, skipped_secret


def sync_index(
    db_path: Path | str = DEFAULT_DB_PATH,
    roots: SessionRoots | None = None,
) -> SessionSyncResult:
    started = time.monotonic()
    db_path = Path(db_path)
    roots = roots or SessionRoots.from_environment()
    descriptors, completed_sources, source_errors = discover_sources(roots)
    discovered = {
        (descriptor.harness, descriptor.session_id): descriptor
        for descriptor in descriptors
    }
    indexed_fingerprints = {
        key: "%s:%s" % (INDEX_FORMAT_VERSION, descriptor.fingerprint)
        for key, descriptor in discovered.items()
    }
    counts = Counter(descriptor.harness for descriptor in descriptors)

    conn = connect(db_path)
    previous_rows = conn.execute(
        "SELECT harness, session_id, fingerprint, source_path FROM session_sources"
    ).fetchall()
    previous = {
        (row[0], row[1]): row[2]
        for row in previous_rows
    }
    changed = [
        descriptor
        for key, descriptor in discovered.items()
        if previous.get(key) != indexed_fingerprints[key]
    ]
    # Missing/unavailable roots and unconfigured sources retain their cache.
    # A successfully scanned empty source may prune its own derived rows only.
    prunable = {(h, sid) for h, sid, _, path in previous_rows
                if any(h == harness and (path == root if is_db else Path(path).is_relative_to(root))
                       for harness, root, is_db in completed_sources)}
    stale = (set(previous) - set(discovered)) & prunable
    indexed_messages = 0
    skipped_secret = 0

    try:
        conn.execute("BEGIN IMMEDIATE")
        for harness, session_id in stale:
            _delete_session(conn, harness, session_id)
        for descriptor in changed:
            key = (descriptor.harness, descriptor.session_id)
            indexed, skipped = _replace_session(
                conn,
                descriptor,
                indexed_fingerprints[key],
            )
            indexed_messages += indexed
            skipped_secret += skipped
        conn.commit()
        enforce_private_database(db_path)
    except Exception:
        conn.rollback()
        enforce_private_database(db_path)
        conn.close()
        enforce_private_database(db_path)
        raise
    enforce_private_database(db_path)
    conn.close()
    enforce_private_database(db_path)

    return SessionSyncResult(
        sessions_by_harness=dict(sorted(counts.items())),
        changed_sessions=len(changed) + len(stale),
        indexed_messages=indexed_messages,
        skipped_secret=skipped_secret,
        elapsed_seconds=time.monotonic() - started,
        source_errors=tuple(source_errors),
    )


def fts_terms(query: str) -> list[str]:
    words = re.findall(r"[a-z0-9][a-z0-9_-]{1,}", query.lower())
    terms = []
    for word in words:
        if word not in terms:
            terms.append(word)
    return terms[:20]


def day_start(value: str) -> float:
    """Local midnight at the start of a YYYY-MM-DD day."""
    return datetime.strptime(value, "%Y-%m-%d").timestamp()


def day_end(value: str) -> float:
    """Local midnight at the end of a YYYY-MM-DD day, so the day is included."""
    return (datetime.strptime(value, "%Y-%m-%d") + timedelta(days=1)).timestamp()


def _query_rows(
    conn: sqlite3.Connection,
    expression: str,
    fetch: int,
    harness: str | None,
    current_harness: str | None,
    current_session_id: str | None,
    since: float | None = None,
    before: float | None = None,
):
    sql = """
        SELECT
            m.harness,
            m.session_id,
            s.title,
            s.cwd,
            s.started_at,
            s.updated_at,
            s.source_path,
            m.role,
            m.timestamp,
            bm25(session_messages_fts) AS score,
            snippet(
                session_messages_fts,
                0,
                '',
                '',
                ' ... ',
                40
            ) AS excerpt,
            m.id
        FROM session_messages_fts
        JOIN session_messages m ON m.id = session_messages_fts.rowid
        JOIN session_sources s
          ON s.harness = m.harness
         AND s.session_id = m.session_id
        WHERE session_messages_fts MATCH ?
    """
    params: list[object] = [expression]
    if harness:
        sql += " AND m.harness = ?"
        params.append(harness)
    if current_harness and current_session_id:
        sql += " AND NOT (m.harness = ? AND (m.session_id = ? OR substr(m.session_id,instr(m.session_id,':')+1)=?))"
        params.extend([current_harness, current_session_id, current_session_id])
    if since is not None or before is not None:
        # A date filter can't place a message with no timestamp, so leave it out.
        sql += " AND m.timestamp > 0"
    if since is not None:
        sql += " AND m.timestamp >= ?"
        params.append(since)
    if before is not None:
        sql += " AND m.timestamp < ?"
        params.append(before)
    sql += " ORDER BY bm25(session_messages_fts), m.timestamp DESC LIMIT ?"
    params.append(fetch)
    return conn.execute(sql, params).fetchall()


def recall_sessions(
    db_path: Path | str,
    query: str,
    *,
    limit: int = 5,
    harness: str | None = None,
    current_harness: str | None = None,
    current_session_id: str | None = None,
    sync: bool = True,
    roots: SessionRoots | None = None,
    all_terms_first: bool = False,
    since: float | None = None,
    before: float | None = None,
) -> list[SessionHit]:
    db_path = Path(db_path)
    source_errors = ()
    refresh_failed = False
    if sync:
        try:
            source_errors = sync_index(db_path, roots).source_errors
        except (OSError, sqlite3.Error):
            refresh_failed = True
            if not db_path.exists():
                return []
    if not db_path.exists():
        return []
    terms = fts_terms(query)
    if not terms:
        return []

    conn = connect_readonly(db_path)
    fetch = max(limit * 12, 24)
    quoted = ['"%s"' % term.replace('"', '""') for term in terms]
    rows = []
    if all_terms_first:
        # The auto-recall hook injects context nobody asked for, so it keeps
        # messages holding every prompt word and broadens only when none do.
        rows = _query_rows(
            conn,
            " ".join(quoted),
            fetch,
            harness,
            current_harness,
            current_session_id,
            since,
            before,
        )
    if not all_terms_first or (not rows and len(terms) > 1):
        # A search matches any term, and bm25 puts messages holding more and
        # rarer terms first. Requiring every term first dropped a message that
        # missed one word whenever a long message held them all.
        rows = _query_rows(
            conn,
            " OR ".join(quoted),
            fetch,
            harness,
            current_harness,
            current_session_id,
            since,
            before,
        )

    grouped = {}
    for row in rows:
        key = (row[0], row[1])
        group = grouped.setdefault(
            key,
            {
                "metadata": row[:7],
                "score": row[9],
                "messages": [],
            },
        )
        group["score"] = min(group["score"], row[9])
        if len(group["messages"]) < MAX_EXCERPTS_PER_SESSION:
            excerpt = " ".join(str(row[10]).split())
            group["messages"].append(
                SessionExcerpt(
                    role=row[7],
                    timestamp=float(row[8] or 0),
                    text=excerpt[:600],
                    position=_message_position(conn, row[0], row[1], row[11]),
                )
            )
    conn.close()

    hits = []
    for group in grouped.values():
        metadata = group["metadata"]
        hits.append(
            SessionHit(
                harness=metadata[0],
                session_id=metadata[1],
                title=metadata[2],
                cwd=metadata[3],
                started_at=float(metadata[4] or 0),
                updated_at=float(metadata[5] or 0),
                source_path=metadata[6],
                score=float(group["score"]),
                messages=tuple(group["messages"]),
                source_status=("source_unavailable" if not Path(metadata[6]).is_file() or any(
                    metadata[6] == error["path"] or Path(metadata[6]).is_relative_to(error["path"])
                    for error in source_errors) else "refresh_failed" if refresh_failed else "available" if sync else "cached"),
            )
        )
    hits.sort(key=lambda hit: (round(hit.score, 2), -hit.updated_at))

    # Codex and other harnesses can preserve the same conversation under more
    # than one session id after a resume, fork, or archive migration. Keep the
    # source rows intact, but do not spend recall slots on identical evidence.
    distinct_hits = []
    seen_excerpts = set()
    for hit in hits:
        excerpt_key = tuple(
            (message.role, " ".join(message.text.lower().split()))
            for message in hit.messages
        )
        if excerpt_key and excerpt_key in seen_excerpts:
            continue
        seen_excerpts.add(excerpt_key)
        distinct_hits.append(hit)
    return distinct_hits[: max(1, limit)]


def _message_position(
    conn: sqlite3.Connection,
    harness: str,
    session_id: str,
    message_id: int,
) -> int:
    # Messages are stored in transcript order, so the row id gives the order.
    return conn.execute(
        "SELECT COUNT(*) + 1 FROM session_messages "
        "WHERE harness = ? AND session_id = ? AND id < ?",
        (harness, session_id, message_id),
    ).fetchone()[0]


def read_session(
    db_path: Path | str,
    session_id: str,
    *,
    harness: str | None = None,
    start: int = 1,
    limit: int = 40,
    max_chars: int = MAX_READ_CHARS,
) -> SessionPage | None:
    """Return a session's indexed dialogue in order, one page at a time."""
    db_path = Path(db_path)
    if not db_path.exists():
        return None
    start = max(1, start)
    limit = min(max(1, limit), MAX_READ_MESSAGES)
    conn = connect_readonly(db_path)
    sql = """
        SELECT harness, session_id, title, cwd, started_at, updated_at, source_path
        FROM session_sources
        WHERE session_id = ?
    """
    params: list[object] = [session_id]
    if harness:
        sql += " AND harness = ?"
        params.append(harness)
    sources = conn.execute(sql + " ORDER BY harness", params).fetchall()
    if len(sources) > 1:
        conn.close()
        raise ValueError(
            "session %s is in more than one harness (%s), pass a harness"
            % (session_id, ", ".join(row[0] for row in sources))
        )
    if not sources:
        conn.close()
        return None
    source = sources[0]
    total = conn.execute(
        "SELECT COUNT(*) FROM session_messages WHERE harness = ? AND session_id = ?",
        (source[0], source[1]),
    ).fetchone()[0]
    rows = conn.execute(
        "SELECT role, timestamp, content FROM session_messages "
        "WHERE harness = ? AND session_id = ? ORDER BY id LIMIT ? OFFSET ?",
        (source[0], source[1], limit, start - 1),
    ).fetchall()
    conn.close()

    messages = []
    used = 0
    for offset, row in enumerate(rows):
        text = str(row[2])
        if messages and used + len(text) > max_chars:
            break
        if len(text) > max_chars:
            text = text[:max_chars] + " ... [message cut at %d characters]" % max_chars
        used += len(text)
        messages.append(
            SessionExcerpt(
                role=row[0],
                timestamp=float(row[1] or 0),
                text=text,
                position=start + offset,
            )
        )
    next_position = start + len(messages)
    return SessionPage(
        session=SessionSummary(
            harness=source[0],
            session_id=source[1],
            title=source[2],
            cwd=source[3],
            started_at=float(source[4] or 0),
            updated_at=float(source[5] or 0),
            source_path=source[6],
            message_count=int(total),
        ),
        messages=tuple(messages),
        next_start=next_position if next_position <= total else None,
    )


def list_sessions(
    db_path: Path | str,
    *,
    limit: int = 20,
    harness: str | None = None,
    sync: bool = False,
    roots: SessionRoots | None = None,
) -> list[SessionSummary]:
    db_path = Path(db_path)
    if sync:
        try:
            sync_index(db_path, roots)
        except (OSError, sqlite3.Error):
            if not db_path.exists():
                return []
    if not db_path.exists():
        return []
    conn = connect_readonly(db_path)
    sql = """
        SELECT
            s.harness,
            s.session_id,
            s.title,
            s.cwd,
            s.started_at,
            s.updated_at,
            s.source_path,
            COUNT(m.id)
        FROM session_sources s
        JOIN session_messages m
          ON m.harness = s.harness
         AND m.session_id = s.session_id
    """
    params: list[object] = []
    if harness:
        sql += " WHERE s.harness = ?"
        params.append(harness)
    sql += (
        " GROUP BY s.harness, s.session_id "
        "ORDER BY s.updated_at DESC LIMIT ?"
    )
    params.append(max(1, limit))
    rows = conn.execute(sql, params).fetchall()
    conn.close()
    return [
        SessionSummary(
            harness=row[0],
            session_id=row[1],
            title=row[2],
            cwd=row[3],
            started_at=float(row[4] or 0),
            updated_at=float(row[5] or 0),
            source_path=row[6],
            message_count=int(row[7]),
        )
        for row in rows
    ]


def format_time(timestamp: float) -> str:
    if not timestamp:
        return "?"
    try:
        return datetime.fromtimestamp(timestamp).astimezone().isoformat(
            timespec="minutes"
        )
    except (OSError, OverflowError, ValueError):
        return "?"


def print_hits(query: str, hits: list[SessionHit]) -> None:
    if not hits:
        print("no session results")
        return
    print('session recall: "%s"' % query)
    print("")
    for index, hit in enumerate(hits, start=1):
        print(
            "%d. [%s] %s  %s"
            % (index, hit.harness, hit.title, format_time(hit.updated_at))
        )
        print("   session: %s" % hit.session_id)
        print("   source: %s [%s]" % (hit.source_path, hit.source_status))
        if hit.cwd:
            print("   cwd: %s" % hit.cwd)
        for excerpt in hit.messages:
            print("   #%d %s: %s" % (excerpt.position, excerpt.role, excerpt.text))
        print("")


def print_session(page: SessionPage) -> None:
    session = page.session
    print(
        "[%s] %s  %s"
        % (session.harness, session.title, format_time(session.updated_at))
    )
    print("   session: %s" % session.session_id)
    print("   source: %s" % session.source_path)
    if session.cwd:
        print("   cwd: %s" % session.cwd)
    if not page.messages:
        print("   no messages there, the session has %d" % session.message_count)
        return
    print(
        "   messages #%d to #%d of %d"
        % (
            page.messages[0].position,
            page.messages[-1].position,
            session.message_count,
        )
    )
    for message in page.messages:
        print("")
        print("#%d %s  %s" % (message.position, message.role, format_time(message.timestamp)))
        print(message.text)
    if page.next_start:
        print("")
        print("more: start from #%d" % page.next_start)


def log_usage(query: str, hits: list[SessionHit]) -> None:
    try:
        timestamp = datetime.now().isoformat(timespec="seconds")
        caller = os.environ.get("AGENT_MEMORY_CALLER", "-")
        top_reference = (
            "session://%s/%s" % (hits[0].harness, hits[0].session_id)
            if hits
            else "NONE"
        )
        with USAGE_LOG_PATH.open("a", encoding="utf-8") as fh:
            fh.write(
                "%s\t%s\t%s\t%s\n"
                % (timestamp, query, top_reference, caller)
            )
    except OSError:
        pass


def _date_argument(parse):
    def check(value: str) -> float:
        try:
            return parse(value)
        except ValueError:
            raise argparse.ArgumentTypeError(
                "use a YYYY-MM-DD date, not %r" % value
            ) from None

    return check


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="session-memory")
    parser.add_argument(
        "--db",
        default=os.environ.get("AGENT_SESSION_DB", str(DEFAULT_DB_PATH)),
    )
    subparsers = parser.add_subparsers(dest="command", required=True)

    sync_parser = subparsers.add_parser("sync")
    sync_parser.set_defaults(command="sync")

    recall_parser = subparsers.add_parser("recall")
    recall_parser.add_argument("query")
    recall_parser.add_argument("-k", type=int, default=5)
    recall_parser.add_argument("--harness")
    recall_parser.add_argument("--current-harness")
    recall_parser.add_argument("--current-session-id")
    recall_parser.add_argument("--no-sync", action="store_true")
    recall_parser.add_argument(
        "--since", type=_date_argument(day_start), metavar="YYYY-MM-DD"
    )
    recall_parser.add_argument(
        "--until", type=_date_argument(day_end), metavar="YYYY-MM-DD"
    )

    read_parser = subparsers.add_parser("read")
    read_parser.add_argument("session_id")
    read_parser.add_argument("--harness")
    read_parser.add_argument("--start", type=int, default=1)
    read_parser.add_argument("-n", type=int, default=40)

    list_parser = subparsers.add_parser("list")
    list_parser.add_argument("-n", type=int, default=20)
    list_parser.add_argument("--harness")
    list_parser.add_argument("--sync", action="store_true")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    db_path = Path(args.db)
    if args.command == "sync":
        result = sync_index(db_path)
        print("session index sync complete")
        for harness, count in result.sessions_by_harness.items():
            print("  %-8s %d" % (harness, count))
        print("changed sessions: %d" % result.changed_sessions)
        print("indexed messages: %d" % result.indexed_messages)
        print("secret skips:     %d" % result.skipped_secret)
        print("elapsed:          %.2f s" % result.elapsed_seconds)
        for error in result.source_errors:
            if error["error"] == "FileNotFoundError":
                continue  # an agent tool that isn't installed here is normal; recall still marks its cached hits
            print("source unavailable (cache retained): %s %s" % (error["path"], error["error"]), file=sys.stderr)
        return 0
    if args.command == "recall":
        hits = recall_sessions(
            db_path,
            args.query,
            limit=max(1, args.k),
            harness=args.harness,
            current_harness=args.current_harness,
            current_session_id=args.current_session_id,
            sync=not args.no_sync,
            since=args.since,
            before=args.until,
        )
        print_hits(args.query, hits)
        log_usage(args.query, hits)
        return 0
    if args.command == "read":
        try:
            page = read_session(
                db_path,
                args.session_id,
                harness=args.harness,
                start=args.start,
                limit=args.n,
            )
        except ValueError as exc:
            print("error: %s" % exc, file=sys.stderr)
            return 1
        if page is None:
            print("error: no indexed session %s" % args.session_id, file=sys.stderr)
            return 1
        print_session(page)
        return 0
    if args.command == "list":
        sessions = list_sessions(
            db_path,
            limit=max(1, args.n),
            harness=args.harness,
            sync=args.sync,
        )
        if not sessions:
            print("no sessions")
            return 0
        for item in sessions:
            print(
                "[%s] %s  %s  %d messages"
                % (
                    item.harness,
                    format_time(item.updated_at),
                    item.title,
                    item.message_count,
                )
            )
            print("  %s" % item.session_id)
        return 0
    return 2


if __name__ == "__main__":
    sys.exit(main())
