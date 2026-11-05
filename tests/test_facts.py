"""brain_remember with key and value: a tracked fact with its source, and a later different value becomes a dispute
the brief reports, never a silent overwrite."""
from __future__ import annotations

import json

import pytest


@pytest.fixture
def provider(plugin, plugin_module, home, tmp_path, monkeypatch):
    history = plugin_module("history")
    empty = tmp_path / "empty"
    empty.mkdir()
    monkeypatch.setattr(plugin, "roots_for", lambda hermes_home, **_: history.roots_for(
        hermes_home, claude=empty / "c", codex=empty / "x", grok=empty / "g"))
    plugin.settings.save(home, {"principal": "Alex Example", "index_sessions": "no"})
    prov = plugin.AgentSiMemoryProvider()
    prov.initialize("sess-f", hermes_home=str(home), platform="cli", agent_context="primary")
    yield prov
    prov.shutdown()


def call(prov, tool, **args):
    return json.loads(prov.handle_tool_call(tool, args))


def test_fact_is_tracked_then_disputed_on_a_different_value(provider):
    provider.on_turn_start(1, "Heads up: the Brisbane office moves to the new lease on 2 February.")
    bad = call(provider, "brain_remember", fact="The office moves on 2 February.", key="Office Lease", value="2 February",
               quote="Brisbane office moves to the new lease on 2 February")
    assert bad["status"] == "refused" and "fact key" in bad["error"]
    half = call(provider, "brain_remember", fact="The office moves on 2 February.", key="office.brisbane.lease_move_date",
                quote="Brisbane office moves to the new lease on 2 February")
    assert half["status"] == "refused" and "go together" in half["error"]

    first = call(provider, "brain_remember", fact="The Brisbane office moves to the new lease on 2 February.",
                 key="office.brisbane.lease_move_date", value="2 February",
                 quote="Brisbane office moves to the new lease on 2 February")
    assert first["status"] == "remembered" and first["fact_status"] == "current", first
    shown = call(provider, "brain_get", id="office.brisbane.lease_move_date")
    fact = shown["facts"][0]
    assert fact["value"] == "2 February" and fact["source"]["id"] == first["source_id"]
    assert "Facts: 1 current, 0 disputed" in provider._brain.brief()

    provider.on_turn_start(2, "Correction, the Brisbane office moves to the new lease on 9 March now.")
    second = call(provider, "brain_remember", fact="The Brisbane office moves to the new lease on 9 March.",
                  key="office.brisbane.lease_move_date", value="9 March",
                  quote="Brisbane office moves to the new lease on 9 March")
    assert second["status"] == "remembered" and second["fact_status"] == "disputed", second
    assert {v["value"] for v in second["values"]} == {"2 February", "9 March"}
    assert "disagree" in second["note"]
    brief = provider._brain.brief()
    assert "Disputed office.brisbane.lease_move_date" in brief and "Ask a person" in brief
    block = provider.prefetch("when does the Brisbane office move?")
    assert "office.brisbane.lease_move_date" in block

    # the same claim again is idempotent
    again = call(provider, "brain_remember", fact="The Brisbane office moves to the new lease on 9 March.",
                 key="office.brisbane.lease_move_date", value="9 March",
                 quote="Brisbane office moves to the new lease on 9 March")
    assert again["status"] == "remembered" and again["claim_id"] == second["claim_id"]
