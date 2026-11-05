"""`hermes agent-si status | demo [--off] | check`, shown while agent-si is the active memory provider.

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
    from .history import History, roots_for

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
    enabled = settings.as_bool(current.get("index_sessions"), True)
    history = History(settings.sessions_db(home), roots_for(home), enabled=enabled)
    if not enabled:
        print("  sessions:    indexing is off (index_sessions = no)")
    else:
        counts = history.counts()
        where = history.db if history.db.is_file() else "no index yet (built after the first turn)"
        print(f"  sessions:    {where}" + (" | " + ", ".join(f"{k} {v}" for k, v in counts.items()) if counts else ""))
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


def main(args) -> int:
    command = getattr(args, "agent_si_command", None)
    home = _home()
    if command == "status":
        return _status(home)
    if command == "demo":
        return _demo(home, bool(getattr(args, "off", False)))
    if command == "check":
        return _check(home)
    print("Usage: hermes agent-si <status|demo [--off]|check>")
    return 2


def register_cli(subparser) -> None:
    """Build the `hermes agent-si` argparse tree (called by Hermes at argparse setup time)."""
    subs = subparser.add_subparsers(dest="agent_si_command")
    subs.add_parser("status", help="Where the brain and the session index are, and whether writes work")
    demo = subs.add_parser("demo", help="Build the Example Co demo brain and select it")
    demo.add_argument("--off", action="store_true", help="Switch back to your own brain")
    subs.add_parser("check", help="Run the engine's integrity check on the brain")
    subparser.set_defaults(func=main)
