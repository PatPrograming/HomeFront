"""eval.py on recorded responses (check C10). Replay only; no AWS call."""

import json
import subprocess
import sys

from conftest import AGENT_DIR

EVAL = AGENT_DIR / "guardrail" / "eval.py"
SAMPLE = AGENT_DIR / "guardrail" / "fixtures" / "recorded_sample.json"
SAFE_ENV = {"PATH": "/usr/bin:/bin", "AWS_CONFIG_FILE": "/dev/null", "AWS_SHARED_CREDENTIALS_FILE": "/dev/null"}


def run_eval(recorded):
    return subprocess.run([sys.executable, str(EVAL), "--from-recorded", str(recorded)],
                          env=SAFE_ENV, capture_output=True, text=True, timeout=60)


def test_sample_replay_prints_counts_and_exits_0():
    result = run_eval(SAMPLE)
    assert result.returncode == 0, result.stderr
    assert "False positives (benign flagged): 0" in result.stdout
    assert "False negatives (attacks missed): 3" in result.stdout
    assert "Scan errors: 0" in result.stdout
    assert "Planted scrape injection flagged: yes" in result.stdout


def test_scan_errors_are_not_counted_as_clean_and_fail_the_run(tmp_path):
    recorded = json.loads(SAMPLE.read_text())
    del recorded["responses"]["email-1"]  # benign: would otherwise look like a true negative
    del recorded["responses"]["email-6"]  # attack: would otherwise look like a false negative
    broken = tmp_path / "broken.json"
    broken.write_text(json.dumps(recorded))
    result = run_eval(broken)
    assert result.returncode != 0
    assert "Scan errors: 2" in result.stdout
    assert "False negatives (attacks missed): 3" in result.stdout  # email-6 not counted as a miss
    rows = {line.split()[0]: line for line in result.stdout.splitlines() if line.startswith("email-")}
    assert "ERROR" in rows["email-1"] and "ERROR" in rows["email-6"]
    assert " TN " not in rows["email-1"]
