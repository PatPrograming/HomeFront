"""create.sh against a fake `aws` executable (check C11). The real AWS CLI is never on PATH."""

import os
import stat
import subprocess
from pathlib import Path

from conftest import AGENT_DIR

SCRIPT = AGENT_DIR / "guardrail" / "create.sh"

FAKE_AWS = r"""#!/bin/bash
# Fake AWS CLI: logs every call and answers from FAKE_* env vars. Never talks to AWS.
echo "$*" >> "$FAKE_LOG"
case "$*" in
  *"list-guardrails"*"--guardrail-identifier"*) echo "${FAKE_LATEST:-None}" ;;
  *"list-guardrails"*) [[ "$FAKE_EXISTS" == 1 ]] && echo gr-fake || echo None ;;
  *"create-guardrail-version"*) echo "${FAKE_NEXT:-1}" ;;
  *"create-guardrail"*) echo gr-fake ;;
  *"get-guardrail"*) echo "${FAKE_STATUS:-READY}" ;;
  *"update-guardrail"*) ;;
  *) echo "fake aws: unexpected call: $*" >&2; exit 9 ;;
esac
"""


def run(tmp_path: Path, *args: str, **fake: str):
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir(exist_ok=True)
    aws = bin_dir / "aws"
    aws.write_text(FAKE_AWS)
    aws.chmod(aws.stat().st_mode | stat.S_IEXEC)
    log = tmp_path / "aws.log"
    log.write_text("")
    env = {
        "PATH": f"{bin_dir}:/usr/bin:/bin",  # no /opt/homebrew/bin or /usr/local/bin: the real CLI is unreachable
        "HOME": str(tmp_path),
        "AWS_CONFIG_FILE": "/dev/null",
        "AWS_SHARED_CREDENTIALS_FILE": "/dev/null",
        "FAKE_LOG": str(log),
        "GUARDRAIL_READY_TRIES": "2",
        **fake,
    }
    result = subprocess.run(["/bin/bash", str(SCRIPT), *args], env=env, capture_output=True, text=True, timeout=60)
    return result, [line for line in log.read_text().splitlines() if line]


def test_no_flag_is_a_dry_run_and_calls_nothing(tmp_path):
    result, calls = run(tmp_path)
    assert result.returncode == 0 and calls == []
    assert "PROMPT_ATTACK" in result.stdout and "--apply" in result.stdout


def test_dry_run_calls_nothing(tmp_path):
    result, calls = run(tmp_path, "--dry-run")
    assert result.returncode == 0 and calls == []
    assert "GUARDRAIL_ID=<dry-run>" in result.stdout


def test_apply_creates_and_publishes_when_missing(tmp_path):
    result, calls = run(tmp_path, "--apply", FAKE_EXISTS="0", FAKE_LATEST="None", FAKE_NEXT="1")
    assert result.returncode == 0, result.stderr
    assert any("create-guardrail " in c or c.endswith("create-guardrail") or " create-guardrail --" in c for c in calls)
    assert any("create-guardrail-version" in c for c in calls)
    assert "GUARDRAIL_ID=gr-fake" in result.stdout and "GUARDRAIL_VERSION=1" in result.stdout


def test_apply_rerun_reuses_the_existing_version(tmp_path):
    result, calls = run(tmp_path, "--apply", FAKE_EXISTS="1", FAKE_LATEST="1")
    assert result.returncode == 0, result.stderr
    assert not any("create-guardrail" in c or "update-guardrail" in c for c in calls)
    assert "GUARDRAIL_ID=gr-fake" in result.stdout and "GUARDRAIL_VERSION=1" in result.stdout


def test_apply_update_publishes_a_new_version(tmp_path):
    result, calls = run(tmp_path, "--apply", "--update", FAKE_EXISTS="1", FAKE_LATEST="1", FAKE_NEXT="2")
    assert result.returncode == 0, result.stderr
    assert any("update-guardrail" in c for c in calls) and any("create-guardrail-version" in c for c in calls)
    assert "GUARDRAIL_VERSION=2" in result.stdout


def test_not_ready_stops_without_publishing(tmp_path):
    result, calls = run(tmp_path, "--apply", FAKE_EXISTS="0", FAKE_STATUS="CREATING")
    assert result.returncode != 0
    assert "CREATING" in result.stderr
    assert not any("create-guardrail-version" in c for c in calls)


def test_unknown_option_fails(tmp_path):
    result, calls = run(tmp_path, "--bogus")
    assert result.returncode == 2 and calls == []
