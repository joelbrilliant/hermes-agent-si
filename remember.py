"""Evidence for brain_remember: the quote must be the user's own words from this conversation, and the two packets
(the captured message as a source, the record citing it) are built here. The engine's evidence gate then checks
every quoted span in the record against the source and refuses words put in the principal's mouth."""
from __future__ import annotations

import hashlib
import re
from datetime import date

MIN_QUOTE_WORDS = 5
MAX_QUOTE_CHARS = 600
MAX_FACT_CHARS = 2000
MAX_TITLE_CHARS = 120
KINDS = ("note", "process", "learning", "gotcha", "reference", "question")

_SPACE = re.compile(r"\s+")
_QUOTES = str.maketrans({"‘": "'", "’": "'", "“": '"', "”": '"', " ": " "})


def normalise(text: str) -> str:
    """Whitespace collapsed, curly quotes straightened, case folded: how a quote is matched to a message."""
    return _SPACE.sub(" ", str(text or "").translate(_QUOTES)).strip().casefold()


def find_quote(quote: str, messages: list[str]) -> str | None:
    """The most recent message that contains the quote, or None."""
    needle = normalise(quote)
    if not needle:
        return None
    for message in reversed(messages):
        if needle in normalise(message):
            return message
    return None


def quote_problem(quote: str) -> str | None:
    words = normalise(quote).split()
    if len(words) < MIN_QUOTE_WORDS:
        return f"quote at least {MIN_QUOTE_WORDS} consecutive words of the user's own message (got {len(words)})"
    if len(quote) > MAX_QUOTE_CHARS:
        return f"the quote is longer than {MAX_QUOTE_CHARS} characters; quote the sentence that carries the fact"
    return None


def slug(text: str, limit: int = 60) -> str:
    out = re.sub(r"[^a-z0-9]+", "-", str(text or "").casefold()).strip("-")
    return out[:limit].strip("-") or "note"


def digest(*parts: str, size: int = 6) -> str:
    return hashlib.sha256("\x1f".join(parts).encode("utf-8")).hexdigest()[:size]


def source_id(session_id: str, message: str) -> str:
    return f"src-hermes-{digest(session_id, message, size=12)}"


def record_id(title: str, today: date, session_id: str, quote: str) -> str:
    return f"rec-{slug(title, 50)}-{today.strftime('%Y%m%d')}-{digest(session_id, quote)}"


def source_packet(*, collector: str, source: str, session_id: str, message: str, author_person_id: str | None,
                  speaker: str, today: date, platform: str) -> dict:
    """The user's message, captured as a user_note the collector agent adds. The brain keeps the words as written."""
    label = f"{speaker} in Hermes ({platform}), {today.isoformat()}"
    return {
        "packet_version": 1,
        "change_id": f"{collector}-{source[4:]}",
        "agent": collector,
        "intent": "Capture the user's message that a remembered fact quotes",
        "ops": [{
            "op": "add_source",
            "row": {
                "id": source,
                "kind": "user_note",
                "title": label,
                "source_date": today.isoformat(),
                "author_person_id": author_person_id,
                "content": message,
            },
        }],
    }


def record_packet(*, agent: str, record: str, source: str, kind: str, title: str, fact: str, quote: str,
                  today: date, session_id: str) -> dict:
    """The record, citing the captured message. The quote sits in the body in quotation marks, where the evidence
    gate checks it against the source."""
    body = fact.strip()
    if normalise(quote) not in normalise(body):
        body = f'{body} The user\'s words: "{quote.strip()}"'
    return {
        "packet_version": 1,
        "change_id": f"{agent}-{record[4:]}",
        "agent": agent,
        "intent": f"Remember: {title[:80]}",
        "ops": [{
            "op": "upsert_record",
            "row": {
                "id": record,
                "kind": kind,
                "status": "active",
                "project_id": "general",
                "title": title,
                "summary": fact.strip()[:300],
                "body": body,
            },
            "source": {"source_id": source, "locator": f"Hermes session {session_id[:12]}, {today.isoformat()}"},
        }],
    }
