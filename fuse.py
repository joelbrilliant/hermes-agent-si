"""The per-turn recall block: the brain's best hits and the best past sessions for the user's message, taken in turn
by rank (reciprocal rank fusion) and written as a few lines. No model anywhere. A search the model asks for is not
merged here: brain_search gives the brain's hits and the sessions as the engine's own search does."""
from __future__ import annotations

import re
from typing import Any

RRF_K = 60
SNIPPET_CHARS = 200
EXCERPT_CHARS = 240


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


def history_item(session: dict, rank: int) -> dict[str, Any]:
    """One past session, in the shape the engine's search gives it (history.item), as the merged list carries it.
    The id ends in the excerpt's message number, so brain_get opens the session at that line."""
    excerpts = session.get("excerpts") or []
    first = excerpts[0] if excerpts else {}
    return {
        "side": "history",
        "id": session.get("id", ""),
        "title": _clean(session.get("title", ""), 80),
        "date": session.get("date") or "",
        "excerpt": _clean(first.get("text", ""), EXCERPT_CHARS),
        "excerpt_role": first.get("role", ""),
        "excerpt_at": first.get("at", ""),
        "rank_history": rank,
    }


def merge(truth: list[dict], history: list[dict], limit: int) -> list[dict[str, Any]]:
    """Fuse the two ranked lists. A key is 'truth:<entity>:<id>' or 'history:<session id>'."""
    items: dict[str, dict[str, Any]] = {}
    truth_keys: list[str] = []
    for rank, hit in enumerate(truth, start=1):
        key = f"truth:{hit.get('entity', '')}:{hit.get('id', '')}"
        if key not in items:
            items[key] = truth_item(hit, rank)
            truth_keys.append(key)
    history_keys: list[str] = []
    for rank, session in enumerate(history, start=1):
        key = "history:" + str(session.get("id", "")).partition("#")[0]
        if key not in items:
            items[key] = history_item(session, rank)
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
