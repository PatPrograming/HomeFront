"""Regenerate schemas/tools.json, policies/*.cedar and samples/dev_token_claims.json from the Python code.

Run from the repo root:  .venv/bin/python packages/mandate-contracts/export_schemas.py
"""

import json
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE / "src"))

from mandate_contracts import tool_schemas  # noqa: E402
from mandate_contracts.cedar import STARTER_POLICIES  # noqa: E402
from mandate_contracts.dev import dev_claims  # noqa: E402


def _write(path: Path, data: object) -> None:
    path.write_text(json.dumps(data, indent=2) + "\n")
    print(f"wrote {path.relative_to(HERE)}")


_write(HERE / "schemas" / "tools.json", tool_schemas())
# One file per policy so AVP setup (WP7) can create each as a static policy.
(HERE / "policies").mkdir(exist_ok=True)
for name, text in STARTER_POLICIES.items():
    (HERE / "policies" / f"{name}.cedar").write_text(text + "\n")
    print(f"wrote policies/{name}.cedar")
# Fixed timestamps so the sample doesn't change on every run.
_write(HERE / "samples" / "dev_token_claims.json", dev_claims(now=1794081600).model_dump())
