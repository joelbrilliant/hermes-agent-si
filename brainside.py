"""The truth side: the vendored agent-si engine, read and written in-process.

One brain is one folder (brain.toml, brain.sqlite3, ROUTER.md). Reads go through the engine's own tool runner, so
Hermes sees exactly what the engine's MCP server would answer. Writes are change packets through the engine's
applier, which runs the evidence gate. Two agents are configured for the plugin: `hermes` writes records, and
`hermesuser`, a collector allowed to author sources for the principal, captures the user's own messages."""
from __future__ import annotations

import json
import re
import shutil
import threading
from pathlib import Path

from . import _vendor  # noqa: F401  (vendor/ on sys.path)
from brain import config as brain_config
from brain import init as brain_init
from brain import reads, schemas, store
from brain.applier import Applier
from brain.collect import run as collect_run

AGENT = "hermes"
COLLECTOR = "hermesuser"
BRAIN_NAME = "brain"
DEMO_NAME = "demo"

AGENTS_TOML = """
# Added by the Hermes agent-si plugin. `hermes` writes records; `hermesuser` captures the user's own messages as
# sources, which is why it may author sources for the principal (a collector of the person's words, not an agent).
[agents.hermes]
kind = "agent"
operations = "*"
approval = "none"
approvers = []
author_principal = false

[agents.hermesuser]
kind = "collector"
manual_sources = true
operations = ["add_source"]
approval = "none"
approvers = []
author_principal = true
"""

PREFIX_ENTITY = {
    "rec": "record", "src": "source", "act": "action", "dec": "decision", "chk": "checklist_item", "per": "person",
    "sys": "system", "thr": "thread", "mtg": "meeting", "sig": "signal", "clm": "claim", "lnk": "thread_link",
}
_FACT_KEY = re.compile(schemas.FACT_KEY)
_PROJECT_ID = re.compile(schemas.PROJECT_ID)


def render_config(name: str, timezone: str, principal: str | None) -> str:
    """The engine's own brain.toml for a new brain, plus this plugin's two agents."""
    return brain_init._render_template(name, timezone, principal) + AGENTS_TOML


def result_dict(result: reads.ToolResult) -> dict:
    """A tool result as a dict: the structured answer, else the text parsed, else the text itself."""
    if result.structured is not None and not result.is_error:
        return result.structured
    try:
        parsed = json.loads(result.text)
    except ValueError:
        parsed = {"text": result.text}
    if isinstance(parsed, dict):
        if result.is_error:
            parsed.setdefault("error", parsed.get("message") or "the brain refused this read")
            parsed.setdefault("code", result.code)
        return parsed
    return {"text": result.text, "error": result.is_error}


class Brain:
    def __init__(self, instance_dir: Path):
        self.dir = Path(instance_dir)
        self._write_lock = threading.Lock()

    # -- existence and creation ---------------------------------------------------------------------

    @property
    def exists(self) -> bool:
        return (self.dir / "brain.toml").is_file() and (self.dir / "brain.sqlite3").is_file()

    @classmethod
    def create(cls, parent: Path, name: str, *, principal: str | None, timezone: str = "UTC") -> tuple["Brain", dict]:
        parent = Path(parent)
        parent.mkdir(parents=True, exist_ok=True)
        outcome = brain_init.init_instance(name, parent, principal=principal, timezone=timezone,
                                           config_text=render_config(name, timezone, principal))
        return cls(parent / name), outcome

    def config(self):
        return brain_config.load_config(self.dir / "brain.toml")

    def principal(self):
        try:
            return self.config().principal
        except Exception:
            return None

    def writable(self) -> tuple[bool, str]:
        """Whether this brain's config lets the plugin write (both agents present, the collector authoring for the
        principal). A brain the user pointed us at may allow reads only."""
        try:
            agents = self.config().agents
        except Exception as exc:
            return False, f"brain.toml could not be read: {exc}"
        missing = [name for name in (AGENT, COLLECTOR) if name not in agents]
        if missing:
            return False, (f"brain.toml has no [agents.{missing[0]}] block; reads work, brain_remember is refused. "
                           "Add the two agent blocks from the plugin README to allow writes.")
        if not agents[COLLECTOR].author_principal:
            return False, f"[agents.{COLLECTOR}] needs author_principal = true to capture the user's own words"
        return True, ""

    # -- reads -----------------------------------------------------------------------------------------

    def run(self, tool: str, arguments: dict | None = None) -> reads.ToolResult:
        return reads.run_tool(self.dir, tool, arguments or {})

    def read(self, tool: str, arguments: dict | None = None) -> dict:
        return result_dict(self.run(tool, arguments))

    def brief(self) -> str:
        result = self.run("brief", {})
        return result.text if not result.is_error else ""

    def search(self, query: str, limit: int = 25) -> list[dict]:
        query = re.sub(r"\s+", " ", str(query or "")).strip()[:200]
        if not query:
            return []
        answer = self.read("search", {"query": query, "limit": max(1, min(int(limit), 25))})
        hits = answer.get("hits") if isinstance(answer, dict) else None
        return list(hits) if isinstance(hits, list) else []

    def get(self, ident: str, offset: int = 0) -> dict:
        ident = str(ident or "").strip()
        prefix = ident.split("-", 1)[0] if "-" in ident else ""
        entity = PREFIX_ENTITY.get(prefix)
        if entity:
            arguments: dict = {"entity": entity, "id": ident}
            if offset:
                arguments["field"] = "body" if entity == "record" else "content"
                arguments["offset"] = max(0, int(offset))
            return self.read("get", arguments)
        if _FACT_KEY.fullmatch(ident):
            return self.read("fact_get", {"key": ident})
        if _PROJECT_ID.fullmatch(ident):
            return self.read("get", {"entity": "project", "id": ident})
        return {"error": (f"{ident!r} is not a brain id. Ids start with rec-, src-, act-, dec-, chk-, per-, sys-, thr-, "
                          "mtg-, sig-, clm- or lnk-, a fact key is dotted (vendor.compliance.instance), and a project "
                          "id is lower-case words with hyphens.")}

    def counts(self) -> dict:
        """Active row counts for status lines; empty when the store can't be read."""
        try:
            con = store.connect_readonly(self.dir / "brain.sqlite3")
        except Exception:
            return {}
        try:
            out = {}
            for table in ("records", "sources", "facts", "actions", "people", "projects"):
                try:
                    out[table] = con.execute(f"SELECT count(*) FROM {table}").fetchone()[0]
                except Exception:
                    continue
            return out
        finally:
            con.close()

    def problems(self) -> list[str]:
        return list(store.check_instance(self.dir))

    # -- writes ----------------------------------------------------------------------------------------

    def apply(self, packet: dict) -> dict:
        """Apply one change packet; the receipt with status applied, rejected or held, and the engine's reason."""
        raw = json.dumps(packet, ensure_ascii=False).encode("utf-8")
        with self._write_lock:
            try:
                applier = Applier(self.dir)
            except Exception as exc:
                return {"status": "unavailable", "error": f"{type(exc).__name__}: {exc}"}
            try:
                outcome = applier.apply(raw)
            except Exception as exc:
                return {"status": "error", "error": f"{type(exc).__name__}: {exc}"}
            finally:
                applier.close()
        receipt = dict(outcome.receipt)
        receipt["exit_code"] = outcome.exit_code
        if outcome.warning:
            receipt["warning"] = outcome.warning
        return receipt

    def apply_file(self, path: Path) -> dict:
        return self.apply(json.loads(Path(path).read_text(encoding="utf-8")))

    def register_fact_key(self, key: str, description: str) -> dict:
        """Fact keys are schema: the engine takes them only from brain.toml through apply-config (upsert_fact_key is
        ungrantable to agents). Append one [[fact_keys]] block, check the config still loads, then apply it."""
        if not re.fullmatch(schemas.FACT_KEY, key):
            return {"status": "rejected", "error": f"{key!r} is not a fact key"}
        toml_path = self.dir / "brain.toml"
        with self._write_lock:
            text = toml_path.read_text(encoding="utf-8")
            if re.search(rf'^\s*key\s*=\s*"{re.escape(key)}"\s*$', text, re.M):
                return {"status": "exists"}
            description = re.sub(r"\s+", " ", str(description or "")).strip()[:200] or key
            block = ("\n[[fact_keys]]\n"
                     f'key = "{key}"\n'
                     'value_type = "text"\n'
                     'unit = ""\n'
                     "stale_after_days = 365\n"
                     f"description = {json.dumps(description, ensure_ascii=False)}\n")
            candidate = toml_path.with_name(".brain.toml.next")
            candidate.write_text(text + block, encoding="utf-8")
            try:
                brain_config.load_config(candidate)
            except Exception as exc:
                candidate.unlink(missing_ok=True)
                return {"status": "rejected", "error": f"the new fact key would break brain.toml: {exc}"}
            candidate.replace(toml_path)
            try:
                outcome = brain_init.apply_config(self.dir)
            except Exception as exc:
                return {"status": "error", "error": f"apply-config failed: {type(exc).__name__}: {exc}"}
        if outcome is None:
            return {"status": "applied", "note": "nothing to apply"}
        receipt = dict(outcome.receipt)
        receipt["exit_code"] = outcome.exit_code
        return receipt


# -- the Example Co demo -------------------------------------------------------------------------------------

def build_demo(parent: Path, demo_dir: Path, extras_dir: Path | None = None) -> tuple[Brain, list[dict]]:
    """Example Co, built as agent-si's own tests build it (init with the demo config, the three notes, the mail
    fixture, the work packet), plus this plugin's two agents and, from extras_dir, a later note that disagrees with
    the setup note so the demo can show a disputed fact. Every name and value is invented. Rebuilt from scratch."""
    parent = Path(parent)
    target = parent / DEMO_NAME
    if target.exists():
        shutil.rmtree(target)
    parent.mkdir(parents=True, exist_ok=True)
    toml = (Path(demo_dir) / "demo.toml").read_text(encoding="utf-8") + AGENTS_TOML
    brain_init.init_instance(DEMO_NAME, parent, principal="Alex Example", timezone="UTC", config_text=toml)
    brain = Brain(target)
    receipts = [brain.apply_file(Path(demo_dir) / "packets" / "1-notes.json")]
    mail = collect_run(target, "mail", Path(demo_dir) / "fixtures" / "mail.json")
    receipts.append({"status": "applied" if mail.exit_code == 0 else "failed", "collector": "mail",
                     "exit_code": mail.exit_code, "warnings": list(mail.warnings)})
    receipts.append(brain.apply_file(Path(demo_dir) / "packets" / "2-work.json"))
    if extras_dir is not None:
        for path in sorted(Path(extras_dir).glob("*.json")):
            receipts.append(brain.apply_file(path))
    return brain, receipts
