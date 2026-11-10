#!/usr/bin/env python3
"""Vendor the engine this plugin runs on, byte for byte, from a pinned checkout.

    python3 tools/vendor.py --agent-si PATH

PATH is a checkout of joelbrilliant/agent-si at the tag to pin. The script copies the modules the plugin imports
(the import closure of the engine's reads, init, applier, config, store, migrate, evidence_gate, schemas, ingress,
validate, collect and sessions), its licence files and the Example Co demo data, then writes vendor/VENDORED.md
with every file's SHA-256 so a reviewer can check the copy against upstream.

Nothing is modified, and the plugin replaces nothing in these modules at run time.
"""
from __future__ import annotations

import argparse
import hashlib
import shutil
import subprocess
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
VENDOR = ROOT / "vendor"
DEMO = ROOT / "demo"

ENGINE_MODULES = (
    "__init__", "advice", "applier", "candidates", "collect", "config", "evidence_gate", "ingress", "init",
    "marks", "migrate", "reads", "redact_ops", "registry", "schemas", "scope", "scrub", "store", "strict", "text",
    "truth", "units", "validate", "versions",
)
ENGINE_DIRS = ("collectors", "migrations", "sessions", "templates")


def sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def describe(repo: Path) -> tuple[str, str]:
    """(remote url, commit sha) of a checkout, for the record."""
    def git(*args: str) -> str:
        done = subprocess.run(["git", "-C", str(repo), *args], capture_output=True, text=True, check=False)
        return done.stdout.strip() if done.returncode == 0 else ""
    return git("config", "--get", "remote.origin.url") or str(repo), git("rev-parse", "HEAD") or "unknown"


def copy_file(src: Path, dest: Path, rows: list[tuple[str, str]]) -> None:
    if src.suffix in (".pyc", ".pyo"):
        return
    dest.parent.mkdir(parents=True, exist_ok=True)
    shutil.copyfile(src, dest)
    rows.append((str(dest.relative_to(ROOT)), sha(dest)))


def copy_tree(src: Path, dest: Path, rows: list[tuple[str, str]]) -> None:
    for path in sorted(src.rglob("*")):
        if path.is_file() and "__pycache__" not in path.parts:
            copy_file(path, dest / path.relative_to(src), rows)


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--agent-si", required=True, type=Path)
    args = ap.parse_args()
    engine_src = args.agent_si / "src" / "brain"
    if not (engine_src / "reads.py").is_file():
        raise SystemExit(f"{args.agent_si} is not an agent-si checkout (no src/brain/reads.py)")
    if not (engine_src / "sessions" / "record.py").is_file():
        raise SystemExit(f"{args.agent_si} has no src/brain/sessions/record.py: the plugin needs agent-si 1.16.1 or newer")

    if VENDOR.exists():
        shutil.rmtree(VENDOR)
    if DEMO.exists():
        shutil.rmtree(DEMO)
    rows: list[tuple[str, str]] = []
    for name in ENGINE_MODULES:
        copy_file(engine_src / f"{name}.py", VENDOR / "brain" / f"{name}.py", rows)
    for name in ENGINE_DIRS:
        copy_tree(engine_src / name, VENDOR / "brain" / name, rows)
    for name in ("LICENSE", "NOTICE"):
        copy_file(args.agent_si / name, VENDOR / f"{name}-agent-si", rows)
    copy_tree(args.agent_si / "examples" / "demo", DEMO, rows)

    si_url, si_sha = describe(args.agent_si)
    lines = [
        "# Vendored code",
        "",
        "Copied byte for byte by `tools/vendor.py`. Nothing here is edited; a change belongs upstream.",
        "",
        f"- agent-si: {si_url} at {si_sha} (`src/brain/`, the modules the plugin imports, plus LICENSE and NOTICE,",
        "  and `examples/demo/` as `demo/`). Past sessions are recorded by the engine's own `brain.sessions`.",
        "",
        "| file | sha256 |",
        "|---|---|",
    ]
    lines += [f"| {path} | {digest} |" for path, digest in rows]
    (VENDOR / "VENDORED.md").write_text("\n".join(lines) + "\n", encoding="utf-8")
    print(f"vendored {len(rows)} files; agent-si {si_sha[:12]}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
