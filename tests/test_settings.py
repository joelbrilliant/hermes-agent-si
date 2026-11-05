"""The settings file is the one Hermes' dashboard panel reads and writes for this provider: the setup wizard, the
Desktop panel, the CLI and the provider share $HERMES_HOME/agent-si/config.json, with values stored the way the
panel stores them (a bool field as JSON true/false). A 0.1.0 home's agent-si.json is adopted once."""
from __future__ import annotations

import importlib.util
import json


def load_schema(plugin_dir):
    """config_schema.py loaded by path, the way Hermes loads it (never as part of the package)."""
    spec = importlib.util.spec_from_file_location("agent_si_config_schema_under_test", plugin_dir / "config_schema.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module.CONFIG_SCHEMA


def test_settings_file_is_the_panels_flat_json(plugin, plugin_dir, home):
    from plugins.memory.config_schema import STORAGE_FLAT_JSON

    schema = load_schema(plugin_dir)
    assert schema.storage == STORAGE_FLAT_JSON
    # hermes_cli/web_routers/memory_providers._flat_json_path: get_hermes_home() / provider.name / "config.json"
    assert plugin.settings.settings_path(home) == home / schema.name / "config.json"
    wizard = {f["key"] for f in plugin.AgentSiMemoryProvider().get_config_schema()}
    assert wizard == {f.key for f in schema.fields} == {"principal", "brain_dir", "index_sessions"}


def test_wizard_values_are_stored_as_the_panel_stores_them(plugin, home):
    prov = plugin.AgentSiMemoryProvider()
    prov.save_config({"principal": " Alex Example ", "brain_dir": "", "index_sessions": "no"}, str(home))
    path = home / "agent-si" / "config.json"
    assert json.loads(path.read_text()) == {"principal": "Alex Example", "index_sessions": False}
    assert prov.get_status_config()["index_sessions"] == "no"
    prov.save_config({"index_sessions": "yes"}, str(home))
    assert json.loads(path.read_text())["index_sessions"] is True
    assert prov.get_status_config()["index_sessions"] == "yes"


def test_a_panel_write_turns_indexing_off_for_the_provider(plugin, plugin_module, home, tmp_path, monkeypatch):
    # What the dashboard's PUT stores for the bool field: JSON false, not the string "false".
    folder = home / "agent-si"
    folder.mkdir()
    (folder / "config.json").write_text(json.dumps({"principal": "Alex Example", "index_sessions": False}))
    history = plugin_module("history")
    empty = tmp_path / "empty"
    empty.mkdir()
    monkeypatch.setattr(plugin, "roots_for", lambda hermes_home, **_: history.roots_for(
        hermes_home, claude=empty / "claude", codex=empty / "codex", grok=empty / "grok"))
    prov = plugin.AgentSiMemoryProvider()
    prov.initialize("sess-1", hermes_home=str(home), platform="cli", agent_context="primary")
    try:
        assert prov._history.enabled is False
        assert prov.get_status_config()["index_sessions"] == "no"
        assert prov._brain.principal().person_id == "per-alex-example"
    finally:
        prov.shutdown()


def test_a_0_1_0_settings_file_is_adopted_once(plugin, home):
    legacy = home / "agent-si.json"
    legacy.write_text(json.dumps({"principal": "Alex Example", "index_sessions": "no"}))
    assert plugin.settings.load(home) == {"principal": "Alex Example", "index_sessions": "no"}
    assert not legacy.exists() and (home / "agent-si" / "config.json").is_file()
    assert plugin.settings.as_bool(plugin.settings.load(home)["index_sessions"]) is False
    plugin.settings.save(home, {"index_sessions": "no"})  # the next save rewrites the value in the panel's form
    assert json.loads((home / "agent-si" / "config.json").read_text())["index_sessions"] is False
