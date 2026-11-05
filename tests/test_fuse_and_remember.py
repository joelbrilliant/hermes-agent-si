"""The pure parts: rank fusion and the quote rules. They need a brain nowhere, only the package import."""
from __future__ import annotations

from datetime import date
from types import SimpleNamespace


def _hit(session_id: str, title: str, text: str):
    return SimpleNamespace(harness="hermes", session_id=session_id, title=title, cwd="", started_at=1.0,
                           updated_at=1_700_000_000.0, source_path="", score=1.0,
                           messages=(SimpleNamespace(role="user", timestamp=1.0, text=text, position=0),))


def test_rrf_prefers_an_item_both_sides_rank(plugin_module):
    fuse = plugin_module("fuse")
    order = [key for key, _ in fuse.rrf([["a", "b", "c"], ["c", "d"]])]
    assert order[0] == "c"  # ranked by both lists
    assert order[1] == "a"  # first on one list beats third on one list


def test_merge_labels_sides_and_keeps_limit(plugin_module):
    fuse = plugin_module("fuse")
    truth = [{"entity": "record", "id": "rec-a", "title": "A", "snippet": "x [y] z", "date": "2026-10-01"}]
    history = [_hit("s1", "Session one", "we moved the launch to March")]
    items = fuse.merge(truth, history, limit=5)
    assert {item["side"] for item in items} == {"truth", "history"}
    assert items[0]["score"] >= items[1]["score"]
    truth_item = next(item for item in items if item["side"] == "truth")
    assert truth_item["snippet"] == "x y z"  # the engine's [match] marks are dropped
    history_item = next(item for item in items if item["side"] == "history")
    assert history_item["id"] == "hermes:s1" and history_item["excerpt"].startswith("we moved")
    assert len(fuse.merge(truth * 3, history, limit=1)) == 1


def test_recall_block_stays_in_budget_and_names_both_sides(plugin_module):
    fuse = plugin_module("fuse")
    truth = [{"entity": "record", "id": f"rec-{n}", "title": "T" * 60, "snippet": "s" * 180, "date": "2026-10-01"}
             for n in range(10)]
    block = fuse.recall_block(fuse.merge(truth, [], limit=10), budget=700)
    assert block.startswith("[agent-si brain]")
    assert len(block) <= 700
    assert "from the brain" in block and "past sessions" in block
    assert fuse.recall_block([]) == ""


def test_quote_rules(plugin_module):
    rem = plugin_module("remember")
    assert rem.quote_problem("too short") is not None
    assert rem.quote_problem("five words are enough here") is None
    messages = ["Hello there.", "We moved the launch to March, and  Sam’s team owns the rollout."]
    assert rem.find_quote("moved the launch to March, and Sam's team", messages) == messages[1]
    assert rem.find_quote("MOVED THE LAUNCH TO MARCH", messages) == messages[1]
    assert rem.find_quote("moved the launch to April", messages) is None


def test_packets_cite_the_captured_message(plugin_module):
    rem = plugin_module("remember")
    today = date(2026, 11, 5)
    source = rem.source_id("sess-1", "We moved the launch to March.")
    record = rem.record_id("Launch is in March", today, "sess-1", "moved the launch to March")
    assert source.startswith("src-hermes-") and record.startswith("rec-launch-is-in-march-20261105-")
    packet = rem.source_packet(collector="hermesuser", source=source, session_id="sess-1",
                               message="We moved the launch to March.", author_person_id="per-alex-example",
                               speaker="Alex Example", today=today, platform="cli")
    row = packet["ops"][0]["row"]
    assert packet["agent"] == "hermesuser" and row["kind"] == "user_note" and row["content"].startswith("We moved")
    packet = rem.record_packet(agent="hermes", record=record, source=source, kind="note", title="Launch is in March",
                               fact="The launch is in March.", quote="moved the launch to March", today=today,
                               session_id="sess-1")
    op = packet["ops"][0]
    assert op["op"] == "upsert_record" and op["source"]["source_id"] == source
    assert '"moved the launch to March"' in op["row"]["body"]
