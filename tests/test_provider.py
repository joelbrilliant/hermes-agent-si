"""The provider end to end in a fresh Hermes home: the brain is created, recall works, remember is gated, the
session index finds past sessions in both Hermes schemas, and the demo brain shows a disputed fact."""
from __future__ import annotations

import json

import pytest


@pytest.fixture
def provider(plugin, plugin_module, home, tmp_path, monkeypatch):
    """An initialised provider whose session roots point at this test's folders only."""
    history = plugin_module("history")
    empty = tmp_path / "empty"
    empty.mkdir()

    def roots(hermes_home, **_):
        return history.roots_for(hermes_home, claude=empty / "claude", codex=empty / "codex", grok=empty / "grok")

    monkeypatch.setattr(plugin, "roots_for", roots)
    plugin.settings.save(home, {"principal": "Alex Example"})
    prov = plugin.AgentSiMemoryProvider()
    assert prov.is_available(), prov.unavailable_reason()
    prov.initialize("sess-1", hermes_home=str(home), platform="cli", agent_context="primary")
    yield prov
    prov.shutdown()


def call(prov, tool, **args):
    return json.loads(prov.handle_tool_call(tool, args))


def test_brain_is_created_with_owner_and_agents(provider, home):
    brain = provider._brain
    assert brain.exists and brain.dir == home / "agent-si" / "brain"
    assert brain.principal().person_id == "per-alex-example"
    writable, why = brain.writable()
    assert writable, why
    block = provider.system_prompt_block()
    assert block.startswith("# agent-si brain") and "Rules for this memory" in block and "brain_remember" in block
    assert provider.get_tool_schemas()[0]["name"] == "brain_search"


def test_prefetch_skips_trivial_prompts_and_reports_recall(provider):
    assert provider.prefetch("ok") == "" and provider.recall_status() is None
    assert provider.prefetch("what is the launch date for the widget") == ""  # empty brain, nothing to say


def test_remember_is_gated_then_searchable(provider):
    provider.on_turn_start(1, "Quick update: we moved the widget launch to March, and Sam's team owns the rollout.")
    # a quote the user never wrote
    out = call(provider, "brain_remember", fact="The launch is in April.", quote="moved the widget launch to April")
    assert out["status"] == "refused" and "not in anything the user wrote" in out["error"]
    # too short a quote
    out = call(provider, "brain_remember", fact="The launch is in March.", quote="launch to March")
    assert out["status"] == "refused" and "consecutive words" in out["error"]
    # the real words
    out = call(provider, "brain_remember", fact="The widget launch is in March. Sam's team owns the rollout.",
               quote="moved the widget launch to March, and Sam's team owns the rollout",
               title="Widget launch moved to March")
    assert out["status"] == "remembered", out
    record_id, source_id = out["record_id"], out["source_id"]
    # the record cites the captured message
    row = call(provider, "brain_get", id=record_id)
    assert row["row"]["id"] == record_id and row["source"]["id"] == source_id
    assert row["source"]["author_person_id"] == "per-alex-example" and row["source"]["kind"] == "user_note"
    source = call(provider, "brain_get", id=source_id)
    assert "moved the widget launch to March" in json.dumps(source)
    # search finds it, labelled truth, and the recall block carries it
    found = call(provider, "brain_search", query="widget launch March")
    assert found["truth_hits"] >= 1 and found["hits"][0]["id"] == record_id and found["hits"][0]["side"] == "truth"
    block = provider.prefetch("when is the widget launch?")
    assert record_id in block and provider.recall_status().count >= 1
    # remembering the same thing again is idempotent (same ids, same receipt)
    again = call(provider, "brain_remember", fact="The widget launch is in March. Sam's team owns the rollout.",
                 quote="moved the widget launch to March, and Sam's team owns the rollout",
                 title="Widget launch moved to March")
    assert again["status"] == "remembered" and again["record_id"] == record_id


def test_gate_refuses_words_put_in_the_owners_mouth(provider):
    provider.on_turn_start(1, "The vendor review is done and the backup restore test passed on Friday.")
    out = call(provider, "brain_remember",
               fact="Alex decided that the vendor review is finished and no further checks are needed.",
               quote="vendor review is done and the backup restore test passed")
    assert out["status"] == "refused" and out["step"] == "record"
    assert "attributes words or a decision to Alex Example" in out["error"]
    neutral = call(provider, "brain_remember",
                   fact="The vendor review is done. The backup restore test passed on Friday.",
                   quote="vendor review is done and the backup restore test passed")
    assert neutral["status"] == "remembered", neutral


def test_bad_ids_and_unknown_tools_answer_cleanly(provider):
    assert "error" in call(provider, "brain_get", id="nonsense")
    assert "error" in call(provider, "brain_search", query="")
    assert "no tool" in json.loads(provider.handle_tool_call("brain_forget", {}))["error"]
    missing = call(provider, "brain_get", id="rec-not-here")
    assert "error" in missing or missing.get("code")


@pytest.mark.parametrize("current_schema", [False, True])
def test_history_indexes_both_hermes_schemas(provider, home, make_state_db, current_schema):
    make_state_db(home / "state.db", current_schema=current_schema, sessions=[
        {"id": "old-1", "surface": "cli", "title": "Rollout planning",
         "messages": [("user", "Let's plan the kestrel rollout for the Brisbane office"),
                      ("assistant", "The kestrel rollout plan has three steps.")]},
        {"id": "cron-1", "surface": "cron", "title": "nightly",
         "messages": [("user", "kestrel cron noise"), ("assistant", "done")]},
        {"id": "sess-1", "surface": "cli", "title": "live",
         "messages": [("user", "kestrel live chat in this very session"), ("assistant", "ok")]},
    ])
    result = provider._history.refresh()
    assert result.sessions_by_harness.get("hermes") == 2  # the cron session is not a human session
    found = call(provider, "brain_search", query="kestrel rollout", side="history")
    assert found["history_hits"] == 1 and found["hits"][0]["id"] == "hermes:old-1"  # the live session is left out
    page = call(provider, "brain_get", id="hermes:old-1")
    assert page["message_count"] == 2 and page["messages"][0]["text"].startswith("Let's plan the kestrel")
    merged = call(provider, "brain_search", query="kestrel rollout")
    assert merged["history_hits"] == 1
    block = provider.prefetch("how did the kestrel rollout go?")
    assert "hermes:old-1" in block


def test_demo_brain_has_a_disputed_fact(plugin_module, plugin_dir, tmp_path):
    brainside = plugin_module("brainside")
    brain, receipts = brainside.build_demo(tmp_path, plugin_dir / "demo", plugin_dir / "demo-extras")
    assert [r.get("status") for r in receipts] == ["applied"] * len(receipts), receipts
    fact = brain.get("vendor.compliance.instance")
    text = json.dumps(fact)
    assert "disputed" in text and "eu" in text and "us" in text, text[:600]
    assert "Disputed vendor.compliance.instance" in brain.brief()
    writable, why = brain.writable()
    assert writable, why
    hits = brain.search("Robin renewal quote")
    assert hits, "the mail fixture's thread should be searchable"
