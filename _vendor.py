"""Put vendor/ on sys.path, last, so the vendored engine (`brain`) and session index (`session_continuity`,
`session_adapters`, `db_permissions`, `ingest`) import as upstream wrote them. Appending keeps every module Hermes
or its environment already has ahead of ours; none of these names exists there (checked 5 November 2026)."""
from __future__ import annotations

import sys
from pathlib import Path

VENDOR = Path(__file__).resolve().parent / "vendor"

if str(VENDOR) not in sys.path:
    sys.path.append(str(VENDOR))
