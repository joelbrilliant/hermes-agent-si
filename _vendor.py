"""Put vendor/ on sys.path, last, so the vendored engine (`brain`, which also records past sessions) imports as
upstream wrote it. Appending keeps every module Hermes or its environment already has ahead of ours; the name
doesn't exist there (checked 5 November 2026)."""
from __future__ import annotations

import sys
from pathlib import Path

VENDOR = Path(__file__).resolve().parent / "vendor"

if str(VENDOR) not in sys.path:
    sys.path.append(str(VENDOR))
