#!/usr/bin/env python3
"""Read-only adapters for local agent session stores."""

from __future__ import annotations

import json
import os
import sqlite3
from dataclasses import dataclass, replace
from datetime import datetime
from pathlib import Path
from typing import Iterable


HUMAN_HERMES_SOURCES = {
    "acp",
    "api_server",
    "cli",
    "desktop",
    "discord",
    "signal",
    "slack",
    "teams",
    "telegram",
    "tui",
    "webhook",
    "weixin",
    "whatsapp",
}

CODEX_BOOTSTRAP_PREFIXES = (
    "# AGENTS.md instructions",
    "<app-context>",
    "<apps_instructions>",
    "<collaboration_mode>",
    "<environment_context>",
    "<permissions instructions>",
    "<plugins_instructions>",
    "<recommended_plugins>",
    "<skills_instructions>",
)

GENERATED_MESSAGE_PREFIXES = (
    "[CONTEXT COMPACTION",
    "[Context compaction",
    "[System note:",
    "[Your active task list was preserved across context compression]",
    "[IMPORTANT: Background process",
    "You've reached the maximum number of tool-calling iterations allowed.",
    "Review the conversation above and update the skill library",
    "Review the conversation above and consider saving to memory",
    "Review the conversation above and update two things:",
)
GENERATED_USER_ENVELOPE_PREFIX = "<user_info"
ACTIVE_TASK_LIST_MARKER = (
    "[Your active task list was preserved across context compression]"
)


def sources_file() -> Path:
    """The optional list of extra Hermes profiles, session-sources.json in the config folder (not beside this
    script, so an upgrade keeps it): {"hermes_profiles": {"work": "~/.hermes/profiles/work/state.db"}}."""
    base = os.environ.get("XDG_CONFIG_HOME") or str(Path.home() / ".config")
    return Path(base) / "agent-memory" / "session-sources.json"


@dataclass(frozen=True)
class SessionRoots:
    hermes_db: Path
    claude_root: Path
    codex_root: Path
    grok_root: Path
    hermes_profiles: tuple[tuple[str, Path], ...] = ()

    @classmethod
    def from_environment(cls) -> "SessionRoots":
        home = Path.home()
        hermes_home = Path(
            os.environ.get("HERMES_HOME", str(home / ".hermes"))
        )
        profiles = ()
        config_path = os.environ.get("AGENT_SESSION_SOURCES_FILE")
        if config_path or not (os.environ.get("HERMES_SESSION_DB") or os.environ.get("HERMES_HOME")):
            config = Path(config_path) if config_path else sources_file()
            if config.exists():
                data = json.loads(config.read_text())
                profiles = tuple((name, Path(path).expanduser())
                                 for name, path in data.get("hermes_profiles", {}).items())
                if any(not name or ":" in name or name == "default" for name, _ in profiles):
                    raise ValueError("Hermes profile names must be nonempty, unique namespaces without colons")
        return cls(
            hermes_profiles=profiles,
            hermes_db=Path(
                os.environ.get(
                    "HERMES_SESSION_DB",
                    str(hermes_home / "state.db"),
                )
            ),
            claude_root=Path(
                os.environ.get(
                    "CLAUDE_PROJECTS_ROOT",
                    str(home / ".claude" / "projects"),
                )
            ),
            codex_root=Path(
                os.environ.get(
                    "CODEX_SESSIONS_ROOT",
                    str(home / ".codex" / "sessions"),
                )
            ),
            grok_root=Path(
                os.environ.get(
                    "GROK_SESSIONS_ROOT",
                    str(home / ".grok" / "sessions"),
                )
            ),
        )


@dataclass(frozen=True)
class SessionDescriptor:
    harness: str
    session_id: str
    source_path: str
    fingerprint: str
    title: str
    cwd: str
    started_at: float
    updated_at: float
    native_session_id: str = ""


@dataclass(frozen=True)
class RawMessage:
    message_key: str
    role: str
    content: str
    timestamp: float


def parse_timestamp(value) -> float:
    if isinstance(value, (int, float)):
        return float(value)
    if not isinstance(value, str) or not value:
        return 0.0
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00")).timestamp()
    except ValueError:
        return 0.0


def file_fingerprint(path: Path, extra_path: Path | None = None) -> str:
    parts = []
    for candidate in (path, extra_path):
        if candidate is None:
            continue
        try:
            stat = candidate.stat()
        except OSError:
            parts.append("missing")
            continue
        parts.append("%d:%d" % (stat.st_mtime_ns, stat.st_size))
    return "|".join(parts)


def read_json_lines(path: Path) -> Iterable[tuple[int, dict]]:
    try:
        with path.open("r", encoding="utf-8", errors="replace") as fh:
            for line_number, line in enumerate(fh, start=1):
                try:
                    row = json.loads(line)
                except (json.JSONDecodeError, ValueError):
                    continue
                if isinstance(row, dict):
                    yield line_number, row
    except OSError:
        raise


def text_parts(content, allowed_types: set[str]) -> list[str]:
    if isinstance(content, str):
        return [content]
    if not isinstance(content, list):
        return []
    parts = []
    for item in content:
        if not isinstance(item, dict):
            continue
        if item.get("type") not in allowed_types:
            continue
        text = item.get("text")
        if isinstance(text, str) and text.strip():
            parts.append(text)
    return parts


def is_generated_user_envelope(role: str, content: str) -> bool:
    return (
        role == "user"
        and content.lstrip().lower().startswith(GENERATED_USER_ENVELOPE_PREFIX)
    )


def clean_message_content(role: str, content: str) -> str:
    """Remove harness-generated context while preserving genuine user text."""
    if not isinstance(content, str):
        return ""
    cleaned = content.strip()
    if not cleaned:
        return ""
    if cleaned.startswith(GENERATED_MESSAGE_PREFIXES):
        return ""
    if is_generated_user_envelope(role, cleaned):
        return ""
    if role == "user":
        marker_at = cleaned.find(ACTIVE_TASK_LIST_MARKER)
        if marker_at >= 0 and (
            marker_at == 0 or cleaned[marker_at - 1] == "\n"
        ):
            cleaned = cleaned[:marker_at].rstrip()
    return cleaned


def discover_hermes(path: Path, *, strict=False) -> list[SessionDescriptor]:
    """Hermes sessions a person took part in. Older state.db files carry the surface (cli, telegram...) in
    sessions.source. Current Hermes writes source 'local' and the surface in a created_source column, so when that
    column exists the query also takes source = 'local' rows whose created_source is a human surface. Rows that
    still carry the surface in source, such as ones migrated from the older layout, match either way."""
    if not path.is_file():
        if strict:
            raise FileNotFoundError(path)
        return []
    sources = tuple(sorted(HUMAN_HERMES_SOURCES))
    placeholders = ",".join("?" for _ in sources)
    try:
        conn = sqlite3.connect("file:%s?mode=ro" % path, uri=True)
        try:
            columns = {row[1] for row in conn.execute("PRAGMA table_info(sessions)")}
            if "created_source" in columns:
                surface = "COALESCE(NULLIF(s.created_source, ''), s.source)"
                where = "(s.source IN (%s) OR (s.source = 'local' AND s.created_source IN (%s)))" % (
                    placeholders,
                    placeholders,
                )
                params = sources + sources
            else:
                surface = "s.source"
                where = "s.source IN (%s)" % placeholders
                params = sources
            rows = conn.execute(
                """
                SELECT
                    s.id,
                    %s,
                    COALESCE(s.title, ''),
                    COALESCE(s.cwd, ''),
                    s.started_at,
                    COALESCE(MAX(m.timestamp), s.ended_at, s.started_at),
                    COALESCE(MAX(m.id), 0),
                    COUNT(m.id)
                FROM sessions s
                JOIN messages m ON m.session_id = s.id
                WHERE %s
                  AND m.role IN ('user', 'assistant')
                GROUP BY s.id
                """
                % (surface, where),
                params,
            ).fetchall()
        finally:
            conn.close()
    except sqlite3.Error:
        if strict:
            raise
        return []

    descriptors = []
    for (
        session_id,
        source,
        title,
        cwd,
        started_at,
        updated_at,
        max_message_id,
        message_count,
    ) in rows:
        descriptors.append(
            SessionDescriptor(
                harness="hermes",
                session_id=str(session_id),
                source_path=str(path),
                fingerprint="%s:%s" % (max_message_id, message_count),
                title=title or "%s session" % source,
                cwd=cwd or "",
                started_at=float(started_at or 0),
                updated_at=float(updated_at or 0),
            )
        )
    return descriptors


def load_hermes(descriptor: SessionDescriptor) -> list[RawMessage]:
    try:
        conn = sqlite3.connect(
            "file:%s?mode=ro" % descriptor.source_path,
            uri=True,
        )
        rows = conn.execute(
            """
            SELECT id, role, content, timestamp
            FROM messages
            WHERE session_id = ?
              AND role IN ('user', 'assistant')
              AND content IS NOT NULL
            ORDER BY timestamp, id
            """,
            (descriptor.native_session_id or descriptor.session_id,),
        ).fetchall()
        conn.close()
    except sqlite3.Error:
        raise  # Roll back the generation instead of replacing cached messages with none.

    messages = []
    for message_id, role, content, timestamp in rows:
        content = clean_message_content(role, content)
        if not content:
            continue
        messages.append(
            RawMessage(
                message_key=str(message_id),
                role=role,
                content=content,
                timestamp=float(timestamp or 0),
            )
        )
    return messages


def discover_claude(root: Path) -> list[SessionDescriptor]:
    if not root.is_dir():
        return []
    descriptors = []
    for path in root.rglob("*.jsonl"):
        if "subagents" in path.parts:
            continue
        session_id = ""
        cwd = ""
        started_at = 0.0
        updated_at = 0.0
        title = ""
        for _, row in read_json_lines(path):
            if row.get("type") not in {"user", "assistant"}:
                continue
            if (
                row.get("isSidechain") is True
                or row.get("isCompactSummary") is True
                or row.get("isMeta") is True
            ):
                continue
            session_id = str(row.get("sessionId") or path.stem)
            cwd = str(row.get("cwd") or "")
            timestamp = parse_timestamp(row.get("timestamp"))
            if not started_at:
                started_at = timestamp
            updated_at = max(updated_at, timestamp)
            if not title and row.get("type") == "user":
                message = row.get("message") or {}
                content = message.get("content")
                if isinstance(content, str):
                    title = " ".join(content.split())[:120]
            break
        if not session_id:
            continue
        descriptors.append(
            SessionDescriptor(
                harness="claude",
                session_id=session_id,
                source_path=str(path),
                fingerprint=file_fingerprint(path),
                title=title or path.stem,
                cwd=cwd,
                started_at=started_at,
                updated_at=path.stat().st_mtime,
            )
        )
    return descriptors


def load_claude(descriptor: SessionDescriptor) -> list[RawMessage]:
    messages = []
    for line_number, row in read_json_lines(Path(descriptor.source_path)):
        row_type = row.get("type")
        if row_type not in {"user", "assistant"}:
            continue
        if (
            row.get("isSidechain") is True
            or row.get("isCompactSummary") is True
            or row.get("isMeta") is True
        ):
            continue
        message = row.get("message")
        if not isinstance(message, dict):
            continue
        role = message.get("role") or row_type
        if role not in {"user", "assistant"}:
            continue
        parts = text_parts(message.get("content"), {"text"})
        content = "\n".join(part for part in parts if part.strip()).strip()
        if not content:
            continue
        content = clean_message_content(role, content)
        if not content:
            continue
        messages.append(
            RawMessage(
                message_key=str(row.get("uuid") or line_number),
                role=role,
                content=content,
                timestamp=parse_timestamp(row.get("timestamp")),
            )
        )
    return messages


def discover_codex(root: Path) -> list[SessionDescriptor]:
    if not root.is_dir():
        return []
    descriptors = []
    for path in root.rglob("*.jsonl"):
        meta = None
        for _, row in read_json_lines(path):
            if row.get("type") == "session_meta":
                payload = row.get("payload")
                if isinstance(payload, dict):
                    meta = payload
                break
        if not meta:
            continue
        session_id = str(meta.get("id") or meta.get("session_id") or path.stem)
        started_at = parse_timestamp(meta.get("timestamp"))
        descriptors.append(
            SessionDescriptor(
                harness="codex",
                session_id=session_id,
                source_path=str(path),
                fingerprint=file_fingerprint(path),
                title=path.stem,
                cwd=str(meta.get("cwd") or ""),
                started_at=started_at,
                updated_at=path.stat().st_mtime,
            )
        )
    return descriptors


def load_codex(descriptor: SessionDescriptor) -> list[RawMessage]:
    messages = []
    for line_number, row in read_json_lines(Path(descriptor.source_path)):
        if row.get("type") != "response_item":
            continue
        payload = row.get("payload")
        if not isinstance(payload, dict) or payload.get("type") != "message":
            continue
        role = payload.get("role")
        if role not in {"user", "assistant"}:
            continue
        if role == "assistant" and payload.get("phase") == "commentary":
            continue
        allowed = {"input_text"} if role == "user" else {"output_text"}
        parts = text_parts(payload.get("content"), allowed)
        if role == "user":
            parts = [
                part
                for part in parts
                if not part.lstrip().startswith(CODEX_BOOTSTRAP_PREFIXES)
            ]
        content = "\n".join(part for part in parts if part.strip()).strip()
        if not content:
            continue
        content = clean_message_content(role, content)
        if not content:
            continue
        messages.append(
            RawMessage(
                message_key=str(payload.get("id") or line_number),
                role=role,
                content=content,
                timestamp=parse_timestamp(row.get("timestamp")),
            )
        )
    return messages


def discover_grok(root: Path) -> list[SessionDescriptor]:
    if not root.is_dir():
        return []
    descriptors = []
    for path in root.rglob("chat_history.jsonl"):
        session_dir = path.parent
        summary_path = session_dir / "summary.json"
        summary = {}
        try:
            loaded = json.loads(summary_path.read_text(encoding="utf-8"))
            if isinstance(loaded, dict):
                summary = loaded
        except (OSError, json.JSONDecodeError, ValueError):
            pass
        session_id = session_dir.name
        started_at = parse_timestamp(summary.get("created_at"))
        updated_at = (
            parse_timestamp(summary.get("last_active_at"))
            or parse_timestamp(summary.get("updated_at"))
        )
        descriptors.append(
            SessionDescriptor(
                harness="grok",
                session_id=session_id,
                source_path=str(path),
                fingerprint=file_fingerprint(path, summary_path),
                title=str(summary.get("generated_title") or session_id),
                cwd=str(summary.get("git_root_dir") or "").rstrip("/"),
                started_at=started_at,
                updated_at=updated_at or path.stat().st_mtime,
            )
        )
    return descriptors


def load_grok(descriptor: SessionDescriptor) -> list[RawMessage]:
    messages = []
    for line_number, row in read_json_lines(Path(descriptor.source_path)):
        role = row.get("type")
        if role not in {"user", "assistant"}:
            continue
        if role == "user" and row.get("synthetic_reason"):
            continue
        parts = text_parts(row.get("content"), {"text"})
        content = "\n".join(part for part in parts if part.strip()).strip()
        if not content:
            continue
        content = clean_message_content(role, content)
        if not content:
            continue
        messages.append(
            RawMessage(
                message_key=str(line_number),
                role=role,
                content=content,
                timestamp=descriptor.started_at + (line_number / 1000.0),
            )
        )
    return messages


def discover_all(roots: SessionRoots) -> list[SessionDescriptor]:
    return discover_sources(roots)[0]


def discover_sources(roots: SessionRoots):
    """Return records, successfully scanned roots, and explicit source failures."""
    descriptors, completed, errors = [], [], []
    sources = [("hermes", roots.hermes_db, "", discover_hermes),
               ("claude", roots.claude_root, "", discover_claude),
               ("codex", roots.codex_root, "", discover_codex),
               ("grok", roots.grok_root, "", discover_grok)]
    sources += [("hermes", path, profile, discover_hermes) for profile, path in roots.hermes_profiles]
    for harness, root, profile, discover in sources:
        try:
            if not root.exists():
                raise FileNotFoundError(root)
            rows = discover(root, strict=True) if harness == "hermes" else discover(root)
            if profile:
                rows = [replace(row, session_id=profile + ":" + row.session_id,
                                native_session_id=row.session_id) for row in rows]
            descriptors.extend(rows)
            completed.append((harness, str(root), harness == "hermes"))
        except (OSError, sqlite3.Error) as exc:
            errors.append({"harness": harness, "profile": profile or "default",
                           "path": str(root), "error": type(exc).__name__})
    return descriptors, completed, errors


def load_messages(descriptor: SessionDescriptor) -> list[RawMessage]:
    if descriptor.harness == "hermes":
        return load_hermes(descriptor)
    if descriptor.harness == "claude":
        return load_claude(descriptor)
    if descriptor.harness == "codex":
        return load_codex(descriptor)
    if descriptor.harness == "grok":
        return load_grok(descriptor)
    return []
