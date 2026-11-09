"""Merge the truth side (the brain's keyword search) and the history side (past agent sessions) into one list, by
reciprocal rank fusion, and format the result for a tool answer or the per-turn recall block. No model anywhere."""
from __future__ import annotations

import re
from datetime import datetime
from typing import Any

RRF_K = 60
SNIPPET_CHARS = 200
EXCERPT_CHARS = 240
CONTEXT_BEFORE = 2


def rrf(ranked_lists: list[list[str]], k: int = RRF_K) -> list[tuple[str, float]]:
    """Keys ordered by summed 1 / (k + rank). Ties keep the order of first appearance."""
    scores: dict[str, float] = {}
    for ranked in ranked_lists:
        for position, key in enumerate(ranked):
            scores[key] = scores.get(key, 0.0) + 1.0 / (k + position + 1)
    return sorted(scores.items(), key=lambda item: -item[1])


def _clean(text: str, limit: int) -> str:
    text = re.sub(r"\s+", " ", str(text or "")).strip()
    return text if len(text) <= limit else text[: limit - 1].rstrip() + "…"


def _day(timestamp: float) -> str:
    try:
        return datetime.fromtimestamp(float(timestamp)).astimezone().strftime("%Y-%m-%d")
    except (OSError, OverflowError, ValueError, TypeError):
        return ""


def clock_time(timestamp: float) -> str:
    """Local 'YYYY-MM-DD HH:MM', or '' when the time is unknown."""
    try:
        return datetime.fromtimestamp(float(timestamp)).strftime("%Y-%m-%d %H:%M") if float(timestamp or 0) > 0 else ""
    except (OSError, OverflowError, ValueError, TypeError):
        return ""


def truth_item(hit: dict, rank: int) -> dict[str, Any]:
    """One hit of the engine's search tool, as the merged list carries it."""
    return {
        "side": "truth",
        "id": hit.get("id", ""),
        "entity": hit.get("entity", ""),
        "title": hit.get("title", ""),
        "date": hit.get("date") or "",
        "status": hit.get("status") or "",
        "snippet": _clean(re.sub(r"[\[\]]", "", hit.get("snippet", "")), SNIPPET_CHARS),
        "rank_truth": rank,
    }


def history_item(hit: Any, rank: int) -> dict[str, Any]:
    """One SessionHit of the session index, as the merged list carries it."""
    excerpts = list(getattr(hit, "messages", ()) or ())
    first = excerpts[0] if excerpts else None
    item = {
        "side": "history",
        "id": f"{hit.harness}:{hit.session_id}",
        "harness": hit.harness,
        "session_id": hit.session_id,
        "title": _clean(hit.title, 80),
        "date": _day(getattr(hit, "updated_at", 0) or getattr(hit, "started_at", 0)),
        "excerpt": _clean(first.text, EXCERPT_CHARS) if first is not None else "",
        "excerpt_role": first.role if first is not None else "",
        "excerpt_at": clock_time(getattr(first, "timestamp", 0)) if first is not None else "",
        "rank_history": rank,
    }
    position = int(getattr(first, "position", 0) or 0)
    if position:
        # brain_get's offset that opens the session a few messages before the excerpt.
        item["open_at"] = max(0, position - 1 - CONTEXT_BEFORE)
    return item


def merge(truth: list[dict], history: list[Any], limit: int) -> list[dict[str, Any]]:
    """Fuse the two ranked lists. A key is 'truth:<entity>:<id>' or 'history:<harness>:<session_id>'."""
    items: dict[str, dict[str, Any]] = {}
    truth_keys: list[str] = []
    for rank, hit in enumerate(truth, start=1):
        key = f"truth:{hit.get('entity', '')}:{hit.get('id', '')}"
        if key not in items:
            items[key] = truth_item(hit, rank)
            truth_keys.append(key)
    history_keys: list[str] = []
    for rank, hit in enumerate(history, start=1):
        key = f"history:{hit.harness}:{hit.session_id}"
        if key not in items:
            items[key] = history_item(hit, rank)
            history_keys.append(key)
    merged = []
    for key, score in rrf([truth_keys, history_keys])[:limit]:
        item = dict(items[key])
        item["score"] = round(score, 5)
        merged.append(item)
    return merged


def _line(item: dict[str, Any]) -> str:
    if item["side"] == "truth":
        head = f"- {item['entity']} {item['id']}"
        if item.get("date"):
            head += f" ({item['date']})"
        text = item.get("title", "")
        if item.get("snippet"):
            text += f": {item['snippet']}"
        return f"{head} {text}".rstrip()
    head = f"- session {item['id']}"
    if item.get("date"):
        head += f" ({item['date']})"
    text = item.get("title", "")
    if item.get("excerpt"):
        text += f": {item['excerpt_role']} said \"{item['excerpt']}\""
    return f"{head} {text}".rstrip()


def recall_block(items: list[dict[str, Any]], budget: int = 1600) -> str:
    """The per-turn recall text: a header, one line per hit, a closing instruction; cut to the budget by whole lines."""
    if not items:
        return ""
    truth_n = sum(1 for item in items if item["side"] == "truth")
    history_n = len(items) - truth_n
    header = (f"[agent-si brain] {len(items)} hits for this message: {truth_n} from the brain, {history_n} from past "
              "sessions. Open one with brain_get before relying on it; answer facts only from sourced rows.")
    lines = [header]
    for item in items:
        line = _line(item)
        if sum(len(x) + 1 for x in lines) + len(line) > budget:
            break
        lines.append(line)
    return "\n".join(lines)
