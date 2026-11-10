"""`hermes agent-si status | demo [--off] | check | forget <session id>`, shown while agent-si is the active memory
provider.

Hermes imports this file on its own (never the provider module), so it stays light until a command runs."""
from __future__ import annotations

import json
import os
import sys
from pathlib import Path


def _home() -> Path:
    try:
        from hermes_constants import get_hermes_home

        return Path(get_hermes_home())
    except Exception:
        return Path(os.environ.get("HERMES_HOME") or Path.home() / ".hermes").expanduser()


def _status(home: Path) -> int:
    from . import settings
    from .brainside import Brain
    from .history import History, record_path, roots_for

    current = settings.load(home)
    folder, chosen = settings.brain_dir(home, current)
    brain = Brain(folder)
    print(f"agent-si memory provider\n  Hermes home: {home}\n  settings:    {settings.settings_path(home)}")
    print(f"  brain:       {folder}" + (" (chosen in settings)" if chosen else "") + ("" if brain.exists else "  [not created yet; it is made on the first session]"))
    if brain.exists:
        principal = brain.principal()
        print(f"  owner:       {principal.display_name + ' (' + principal.person_id + ')' if principal else 'not set (attribution rule off)'}")
        counts = brain.counts()
        if counts:
            print("  rows:        " + ", ".join(f"{k} {v}" for k, v in counts.items()))
        writable, why = brain.writable()
        print(f"  writes:      {'allowed (brain_remember works)' if writable else why}")
        problems = brain.problems()
        print(f"  check:       {'no problems' if not problems else '; '.join(problems)}")
    wanted = settings.as_bool(current.get("index_sessions"), True)
    recorded = brain.sessions_setting()[0] if brain.exists else True
    if not wanted:
        print("  sessions:    not recorded (index_sessions = no)")
    elif not recorded:
        print("  sessions:    not recorded (this brain's brain.toml says record = false under [sessions])")
    elif not brain.exists:
        print("  sessions:    recorded beside the brain once it exists")
    else:
        history = History(record_path(brain.dir), roots_for(home), zone=brain.timezone())
        held = history.status()
        if held.get("exists"):
            per_tool = ", ".join(f"{tool} {count}" for tool, count in held["sessions"].items()) or "none yet"
            print(f"  sessions:    {history.db} | {per_tool} | {held['messages']} messages")
        else:
            print(f"  sessions:    {history.db} [no record yet; it is built in the background from the first turn]")
    old = settings.old_sessions_index(home)
    if old.is_file():
        print(f"  left over:   {old} is the session index 0.1.x built. Nothing reads it now, so it is yours to delete.")
    print("  model:       none   network: none   dependencies: none")
    return 0


def _demo(home: Path, off: bool) -> int:
    from . import settings
    from .brainside import build_demo

    here = Path(__file__).resolve().parent
    if off:
        current = settings.load(home)
        if str(current.get("brain_dir") or "") == str(settings.demo_dir(home)):
            settings.save(home, {"brain_dir": ""})
            print("agent-si: back to your own brain. Start a new Hermes session.")
        else:
            print("agent-si: the demo brain was not selected; nothing changed.")
        return 0
    brain, receipts = build_demo(settings.data_dir(home), here / "demo", here / "demo-extras")
    failed = [r for r in receipts if r.get("status") not in ("applied",)]
    settings.save(home, {"brain_dir": str(brain.dir)})
    print(f"agent-si: Example Co demo brain built at {brain.dir} and selected.")
    print("  Every name, host, date and value in it is invented. `hermes agent-si demo --off` switches back.")
    print("  Start a new Hermes session, then try: Which regional instance does Example Co use? "
          "Has Alex replied to Robin's email of 2 September?")
    for receipt in failed:
        print(f"  step did not apply: {json.dumps(receipt)[:300]}", file=sys.stderr)
    return 1 if failed else 0


def _check(home: Path) -> int:
    from . import settings
    from .brainside import Brain

    folder, _ = settings.brain_dir(home)
    brain = Brain(folder)
    if not brain.exists:
        print(f"agent-si: no brain at {folder} yet")
        return 1
    problems = brain.problems()
    print("agent-si: no problems" if not problems else "agent-si: " + "\n  ".join(problems))
    return 1 if problems else 0


def _forget(home: Path, session: str) -> int:
    from . import settings
    from .brainside import Brain
    from .history import History, record_path, roots_for

    folder, _ = settings.brain_dir(home)
    brain = Brain(folder)
    if not brain.exists:
        print(f"agent-si: no brain at {folder} yet, so no record of past sessions either")
        return 1
    outcome = History(record_path(brain.dir), roots_for(home), zone=brain.timezone()).forget(session)
    if outcome.get("error"):
        print(f"agent-si: {outcome['error']}")
        return 1
    print(f"agent-si: {outcome['session']} is out of the record ({outcome['messages_removed']} messages removed) and "
          "stays out. Its own tool still has whatever it kept.")
    return 0


def main(args) -> int:
    command = getattr(args, "agent_si_command", None)
    home = _home()
    if command == "status":
        return _status(home)
    if command == "demo":
        return _demo(home, bool(getattr(args, "off", False)))
    if command == "check":
        return _check(home)
    if command == "forget":
        return _forget(home, str(getattr(args, "session", "") or ""))
    print("Usage: hermes agent-si <status|demo [--off]|check|forget <session id>>")
    return 2


def register_cli(subparser) -> None:
    """Build the `hermes agent-si` argparse tree (called by Hermes at argparse setup time)."""
    subs = subparser.add_subparsers(dest="agent_si_command")
    subs.add_parser("status", help="Where the brain and its record of past sessions are, and whether writes work")
    demo = subs.add_parser("demo", help="Build the Example Co demo brain and select it")
    demo.add_argument("--off", action="store_true", help="Switch back to your own brain")
    subs.add_parser("check", help="Run the engine's integrity check on the brain")
    forget = subs.add_parser("forget", help="Remove one past session from the record, and keep it out")
    forget.add_argument("session", help="A session id as brain_search shows it, such as hermes:20261110_091500_ab12")
    subparser.set_defaults(func=main)
