"""Dashboard config panel for the agent-si memory provider (Desktop, Capabilities, Tools, Memory). Loaded by path,
never imported as a package, so it imports only Hermes' pure-data schema module. Values live in
$HERMES_HOME/agent-si.json, the same file the setup wizard writes through save_config()."""
from plugins.memory.config_schema import (
    KIND_BOOL,
    KIND_TEXT,
    STORAGE_FLAT_JSON,
    ProviderConfigSchema,
    ProviderField,
)

CONFIG_SCHEMA = ProviderConfigSchema(
    name="agent-si",
    label="agent-si brain",
    storage=STORAGE_FLAT_JSON,
    docs_url="https://github.com/joelbrilliant/hermes-agent-si#config",
    fields=(
        ProviderField(
            key="principal",
            label="Your name",
            kind=KIND_TEXT,
            placeholder="Alex Example",
            description="The brain's owner. The evidence gate refuses facts that put words in this person's mouth "
                        "without a quote from something they wrote.",
            inline=True,
        ),
        ProviderField(
            key="brain_dir",
            label="Brain folder",
            kind=KIND_TEXT,
            placeholder="(default: agent-si/brain inside this Hermes home)",
            description="An existing brain to use instead of the one created on first run. Its brain.toml needs the "
                        "two agent blocks from the README for writes; reads work either way.",
        ),
        ProviderField(
            key="index_sessions",
            label="Index past agent sessions",
            kind=KIND_BOOL,
            default="true",
            description="Search your past Hermes, Claude Code, Codex and Grok sessions on this machine alongside the "
                        "brain. Messages that look like secrets are never indexed.",
            inline=True,
        ),
    ),
)
