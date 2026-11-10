"""The provider end to end in a fresh Hermes home: the brain is created, recall works, remember is gated, the
engine's session record beside the brain finds past sessions in both Hermes schemas, and the demo brain shows a
disputed fact."""
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
    # search finds it as a row of the brain, and the recall block carries it
    found = call(provider, "brain_search", query="widget launch March")
    assert found["hits"][0]["id"] == record_id and found["hits"][0]["entity"] == "record"
    block = provider.prefetch("when is the widget launch?")
    assert record_id in block and provider.recall_status().count >= 1
    # dates keep the brain's rows dated inside them
    today = found["hits"][0]["date"]
    assert call(provider, "brain_search", query="widget launch March", since=today)["hits"]
    assert call(provider, "brain_search", query="widget launch March", until="2000-01-01")["hits"] == []
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


def session_ids(answer: dict) -> list[str]:
    """The sessions of a brain_search answer, without the message number each id ends in."""
    return [session["id"].partition("#")[0] for session in answer.get("sessions", [])]


@pytest.mark.parametrize("current_schema", [False, True])
def test_history_records_both_hermes_schemas(provider, home, make_state_db, current_schema):
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
    assert result.sessions.get("hermes") == 2  # the cron session is not a human session
    # The record is the engine's own file, beside the brain.
    assert provider._history.db == provider._brain.dir / "sessions.sqlite3" and provider._history.db.is_file()
    found = call(provider, "brain_search", query="kestrel rollout", side="history")
    assert session_ids(found) == ["hermes:old-1"] and found["hits"] == []  # the live session is left out
    page = call(provider, "brain_get", id=found["sessions"][0]["id"])
    assert page["entity"] == "session" and page["row"]["messages"] == 2
    assert "Let's plan the kestrel rollout" in page["row"]["content"]
    both = call(provider, "brain_search", query="kestrel rollout")
    assert session_ids(both) == ["hermes:old-1"]
    block = provider.prefetch("how did the kestrel rollout go?")
    assert "hermes:old-1" in block


def test_a_search_sees_a_session_written_since_the_last_refresh(provider, home, make_state_db):
    # No refresh is asked for here: a search the model asks for brings the record up to date first.
    make_state_db(home / "state.db", current_schema=True, sessions=[
        {"id": "fresh-1", "title": "Petrel", "messages": [("user", "the petrel invoice is due on Friday"),
                                                          ("assistant", "Friday, noted.")]}])
    provider.shutdown()  # the first background refresh has finished
    assert session_ids(call(provider, "brain_search", query="petrel invoice")) == ["hermes:fresh-1"]


def test_search_ranks_by_any_word_and_recall_keeps_every_word_first(provider, home, make_state_db):
    make_state_db(home / "state.db", current_schema=True, sessions=[
        {"id": "long-note", "title": "Handover",
         "messages": [("user", "Handover note: osprey ledger migration status, plus the rota and the lunch order"),
                      ("assistant", "Noted.")]},
        {"id": "short-answer", "title": "Ledger",
         "messages": [("user", "The osprey ledger moved to the shared drive."), ("assistant", "Good.")]},
    ])
    provider._history.refresh()
    found = session_ids(call(provider, "brain_search", query="osprey ledger migration", side="history"))
    assert set(found) == {"hermes:long-note", "hermes:short-answer"} and found[0] == "hermes:long-note"
    # The per-turn recall keeps messages holding every word of the message when one does.
    block = provider.prefetch("osprey ledger migration status")
    assert "hermes:long-note" in block and "hermes:short-answer" not in block


def test_search_by_date_and_the_period_listing(provider, home, make_state_db):
    from datetime import datetime

    make_state_db(home / "state.db", current_schema=True, sessions=[
        {"id": "march-10", "title": "Budget", "started": datetime(2026, 3, 10, 12, 0).timestamp(),
         "messages": [("user", "heron budget agreed at forty"), ("assistant", "Forty it is.")]},
        {"id": "march-12", "title": "Budget again", "started": datetime(2026, 3, 12, 12, 0).timestamp(),
         "messages": [("user", "good morning"), ("assistant", "Morning."),
                      ("user", "heron budget raised to fifty"), ("assistant", "Fifty it is.")]},
    ])
    provider._history.refresh()

    def ids(**args):
        return session_ids(call(provider, "brain_search", side="history", **args))

    assert sorted(ids(query="heron budget")) == ["hermes:march-10", "hermes:march-12"]
    assert ids(query="heron budget", since="2026-03-11") == ["hermes:march-12"]
    assert ids(query="heron budget", until="2026-03-10") == ["hermes:march-10"]
    assert ids(query="heron budget", since="2026-03-13") == []

    # Words that match nothing in the period give the period's sessions, and so does no query at all.
    listed = call(provider, "brain_search", query="what did we talk about", side="history",
                  since="2026-03-12", until="2026-03-12")
    assert session_ids(listed) == ["hermes:march-12"] and "newest first" in listed["warnings"][-1]
    assert ids(since="2026-03-10", until="2026-03-12") == ["hermes:march-12", "hermes:march-10"]
    assert "error" in call(provider, "brain_search", query="heron", since="March")
    assert "error" in call(provider, "brain_search")

    # A session's id ends in the message that matched, and opening it shows that message under its number, who
    # spoke and the local clock time.
    hit = call(provider, "brain_search", query="heron budget raised", side="history", since="2026-03-12")["sessions"][0]
    assert hit["id"] == "hermes:march-12#3" and hit["excerpts"][0]["at"] == "2026-03-12 12:02"
    page = call(provider, "brain_get", id=hit["id"])
    assert "[3] user 2026-03-12 12:02\nheron budget raised to fifty" in page["row"]["content"]
    assert "[4] assistant 2026-03-12 12:03\nFifty it is." in page["row"]["content"]
    assert page["row"]["messages"] == 4 and page["pages"]["content"]["next_offset"] is None
    assert page["row"]["started_at"] == "2026-03-12 12:00" and page["id"] == "hermes:march-12"
    assert "no message 9" in call(provider, "brain_get", id="hermes:march-12#9")["error"]
    assert "error" in call(provider, "brain_get", id="hermes:not-here")


def test_sessions_beside_brain_hits_are_a_few_short_leads(provider, home, make_state_db, plugin_module):
    provider.on_turn_start(1, "For the record, the gannet contract renewal is due on the first of June.")
    kept = call(provider, "brain_remember", fact="The gannet contract renewal is due on 1 June.",
                quote="the gannet contract renewal is due on the first of June", title="Gannet contract renewal")
    assert kept["status"] == "remembered", kept
    # Each session says its own thing: the record shows one session for words repeated across several.
    make_state_db(home / "state.db", current_schema=True, sessions=[
        {"id": f"gannet-{n}", "title": f"Gannet {n}",
         "messages": [("user", f"gannet contract, point {n}: " + "and more about the renewal terms " * 12),
                      ("assistant", f"Understood, point {n} of the renewal terms.")]}
        for n in range(5)])
    provider._history.refresh()
    history = plugin_module("history")
    both = call(provider, "brain_search", query="gannet contract renewal")
    assert both["hits"] and both["hits"][0]["id"] == kept["record_id"]
    # beside the brain's hits: a few sessions, one short line each
    assert len(both["sessions"]) == history.SESSIONS_BESIDE
    assert all(len(s["excerpts"]) == 1 and len(s["excerpts"][0]["text"]) <= history.LEAD_CHARS + 4 for s in both["sessions"])
    assert call(provider, "brain_search", query="gannet contract renewal", side="truth").get("sessions") is None
    # the brain has nothing on these words, so the sessions are the answer and come deeper
    alone = call(provider, "brain_search", query="understood point")
    assert alone["hits"] == [] and len(alone["sessions"]) == history.SESSIONS_ALONE
    assert max(len(s["excerpts"]) for s in alone["sessions"]) > 1


def test_a_forgotten_session_leaves_the_record_and_stays_out(provider, home, make_state_db):
    make_state_db(home / "state.db", current_schema=True, sessions=[
        {"id": "keep-1", "title": "Keep", "messages": [("user", "the dunlin survey starts on Monday"), ("assistant", "ok")]},
        {"id": "drop-1", "title": "Drop", "messages": [("user", "the dunlin password hint is private"), ("assistant", "ok")]},
    ])
    provider._history.refresh()
    assert sorted(session_ids(call(provider, "brain_search", query="dunlin", side="history"))) == [
        "hermes:drop-1", "hermes:keep-1"]
    gone = provider._history.forget("hermes:drop-1#1")
    assert gone == {"status": "forgotten", "session": "hermes:drop-1", "messages_removed": 2}
    # Hermes still has the session, and a refresh doesn't bring it back.
    provider._history.refresh()
    assert session_ids(call(provider, "brain_search", query="dunlin", side="history")) == ["hermes:keep-1"]
    assert "error" in call(provider, "brain_get", id="hermes:drop-1")
    assert "error" in provider._history.forget("rec-not-a-session")


def test_the_numbers_for_sessions_are_the_engines_own(plugin_module):
    """history.py repeats four numbers of the engine's search. A re-vendor that changes them should fail here."""
    history = plugin_module("history")
    from brain import reads

    assert (history.SESSIONS_BESIDE, history.SESSIONS_ALONE, history.LEAD_EXCERPTS, history.LEAD_CHARS,
            history.TITLE_CHARS) == (reads._SESSIONS_BESIDE, reads._SESSIONS_ALONE, reads._SESSION_EXCERPTS_BESIDE,
                                     reads._SESSION_LEAD_CHARS, reads._SESSION_TITLE_CHARS)


def test_hermes_sessions_come_from_the_home_the_provider_was_started_for(plugin_module, tmp_path, monkeypatch):
    """A gateway serving several profiles gives each provider its own home; $HERMES_HOME names only one of them."""
    history = plugin_module("history")
    monkeypatch.setenv("HERMES_HOME", str(tmp_path / "another-home"))
    roots = history.roots_for(tmp_path / "this-home")
    assert roots.hermes_db == tmp_path / "this-home" / "state.db"


def test_no_part_of_the_plugin_is_agent_memory(plugin_dir, provider, home):
    assert not any((plugin_dir / "vendor" / name).exists()
                   for name in ("session_continuity.py", "session_adapters.py", "ingest.py", "db_permissions.py"))
    assert (plugin_dir / "vendor" / "brain" / "sessions" / "record.py").is_file()
    # 0.1.x kept its session index here. It is not made any more.
    assert not (home / "agent-si" / "sessions.db").exists()


def test_recording_off_in_the_settings_leaves_sessions_out(plugin, plugin_module, home, tmp_path, monkeypatch,
                                                           make_state_db):
    history = plugin_module("history")
    empty = tmp_path / "empty"
    empty.mkdir()
    monkeypatch.setattr(plugin, "roots_for", lambda hermes_home, **_: history.roots_for(
        hermes_home, claude=empty / "claude", codex=empty / "codex", grok=empty / "grok"))
    plugin.settings.save(home, {"principal": "Alex Example", "index_sessions": "no"})
    make_state_db(home / "state.db", current_schema=True, sessions=[
        {"id": "quiet-1", "title": "Quiet", "messages": [("user", "the curlew plan is private"), ("assistant", "ok")]}])
    prov = plugin.AgentSiMemoryProvider()
    prov.initialize("sess-1", hermes_home=str(home), platform="cli", agent_context="primary")
    try:
        assert "not recorded" in call(prov, "brain_search", query="curlew plan", side="history")["error"]
        assert call(prov, "brain_search", query="curlew plan").get("sessions") is None
        assert "not recorded" in call(prov, "brain_get", id="hermes:quiet-1")["error"]
        assert not (prov._brain.dir / "sessions.sqlite3").exists()
    finally:
        prov.shutdown()


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
