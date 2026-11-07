from __future__ import annotations

import json
from pathlib import Path
from typing import Any


def default_pack_path() -> Path:
    """Resolve vendored pack relative to the monorepo layout."""
    here = Path(__file__).resolve()
    candidates = [
        here.parents[3] / "atlas-pack" / "data" / "atlas-agentic-pack.json",
        here.parents[4] / "packages" / "atlas-pack" / "data" / "atlas-agentic-pack.json",
        Path.cwd() / "packages" / "atlas-pack" / "data" / "atlas-agentic-pack.json",
    ]
    for path in candidates:
        if path.is_file():
            return path
    raise FileNotFoundError(
        "atlas-agentic-pack.json not found; run packages/atlas-pack/scripts/build_agentic_pack.py"
    )


def load_pack(path: Path | None = None) -> dict[str, Any]:
    pack_path = path or default_pack_path()
    with pack_path.open("r", encoding="utf-8") as f:
        return json.load(f)
