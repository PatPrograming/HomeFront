"""Shared test setup: no AWS access, and `mandate_contracts`, `guardrail_scanner` and the agent importable."""

import os
import socket
import sys
import threading
import time
from pathlib import Path

import pytest
import uvicorn

REPO = Path(__file__).resolve().parents[2]
AGENT_DIR = REPO / "agent"
DATA_DIR = AGENT_DIR / "data"

for p in (
    REPO / "packages" / "mandate-contracts" / "src",
    REPO / "packages" / "guardrail-scanner" / "src",
    AGENT_DIR,
):
    if str(p) not in sys.path:
        sys.path.insert(0, str(p))

# No test may reach real AWS or Bedrock: drop every credential source before anything runs.
# Subprocesses started by tests inherit this environment.
for name in ("AWS_ACCESS_KEY_ID", "AWS_SECRET_ACCESS_KEY", "AWS_SESSION_TOKEN", "AWS_PROFILE"):
    os.environ.pop(name, None)
os.environ["AWS_CONFIG_FILE"] = os.environ["AWS_SHARED_CREDENTIALS_FILE"] = "/dev/null"
os.environ["AWS_EC2_METADATA_DISABLED"] = "true"

TOKEN = "test-mandate-token"


def _free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


@pytest.fixture
def stub():
    """A fresh stub server on a free port. Yields (mcp_url, base_url, state)."""
    from stub_server import create_stub

    app, state = create_stub()
    port = _free_port()
    server = uvicorn.Server(uvicorn.Config(app, host="127.0.0.1", port=port, log_level="warning"))
    thread = threading.Thread(target=server.run, daemon=True)
    thread.start()
    for _ in range(100):
        if server.started:
            break
        time.sleep(0.05)
    base = f"http://127.0.0.1:{port}"
    yield f"{base}/mcp", base, state
    server.should_exit = True
    thread.join(timeout=5)
