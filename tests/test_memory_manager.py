"""The provider driven by Hermes' own MemoryManager, the way an agent session drives it: registration through
register(ctx), initialize_all with a profile home, the system prompt block, per-turn prefetch with the recall
indicator, tool routing and the end-of-turn sync. No model and no network anywhere."""
from __future__ import annotations

import json
import time

import pytest


class _Ctx:
    def __init__(self):
        self.provider = None

    def register_memory_provider(self, provider):
        self.provider = provider


@pytest.fixture
def manager(plugin, plugin_module, home, tmp_path, monkeypatch):
    history = plugin_module("history")
    empty = tmp_path / "empty"
    empty.mkdir()
    monkeypatch.setattr(plugin, "roots_for", lambda hermes_home, **_: history.roots_for(
        hermes_home, claude=empty / "c", codex=empty / "x", grok=empty / "g"))
    plugin.settings.save(home, {"principal": "Alex Example"})
    ctx = _Ctx()
    plugin.register(ctx)
    assert ctx.provider is not None and ctx.provider.name == "agent-si"
    from agent.memory_manager import MemoryManager

    mgr = MemoryManager()
    mgr.add_provider(ctx.provider)
    mgr.initialize_all(session_id="mm-1", hermes_home=str(home), platform="cli", agent_context="primary")
    yield mgr, ctx.provider
    mgr.shutdown_all()


def test_manager_drives_prompt_recall_tools_and_sync(manager):
    mgr, provider = manager
    prompt = mgr.build_system_prompt()
    assert "agent-si brain" in prompt and "Rules for this memory" in prompt
    assert {t["name"] for t in mgr.get_all_tool_schemas()} >= {"brain_search", "brain_get", "brain_remember"}
    assert mgr.has_tool("brain_remember")

    message = "Heads up: the Brisbane office moves to the new lease on 2 February, and Priya signs for it."
    mgr.on_turn_start(1, message)
    assert mgr.prefetch_all(message, session_id="mm-1") == ""  # nothing stored yet, so nothing injected
    assert mgr.describe_recall() == ""

    kept = json.loads(mgr.handle_tool_call("brain_remember", {
        "fact": "The Brisbane office moves to the new lease on 2 February. Priya signs for it.",
        "quote": "Brisbane office moves to the new lease on 2 February",
        "title": "Brisbane office lease move"}))
    assert kept["status"] == "remembered", kept

    refused = json.loads(mgr.handle_tool_call("brain_remember", {
        "fact": "Alex agreed the lease and told Priya to sign it.",
        "quote": "Brisbane office moves to the new lease on 2 February"}))
    assert refused["status"] == "refused" and "attributes words or a decision" in refused["error"]

    mgr.sync_all(message, "Noted.", session_id="mm-1")
    mgr.queue_prefetch_all(message, session_id="mm-1")
    deadline = time.time() + 20
    while mgr.flush_pending(timeout=1.0) is False and time.time() < deadline:
        pass

    block = mgr.prefetch_all("when does the Brisbane office move?", session_id="mm-1")
    assert kept["record_id"] in block
    assert "recalled" in mgr.describe_recall() and "agent-si" in mgr.describe_recall()

    found = json.loads(mgr.handle_tool_call("brain_search", {"query": "Brisbane lease"}))
    assert found["hits"][0]["id"] == kept["record_id"]
    row = json.loads(mgr.handle_tool_call("brain_get", {"id": kept["record_id"]}))
    assert row["source"]["kind"] == "user_note" and row["source"]["author_person_id"] == "per-alex-example"
