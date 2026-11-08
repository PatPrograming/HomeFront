"""Backend/infra/setup.sh adds the office-role policies only when asked, and only when every staffer
has a valid role. Runs against a fake `aws` executable: no AWS credentials, no network."""

import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
SCRIPT = ROOT / "Backend" / "infra" / "setup.sh"
STARTER = ["case-read-same-office", "draft-revise", "email-send-known-recipient", "forbid-bulk-export",
           "forbid-campaign-post", "read-tools", "social-post-official"]
# draft-revise is a starter permit setup.sh creates without the flag: every role holds draft_revise anyway.
# Starter policies added with the agent-permissions change. setup.sh never updates a policy, so each is a
# new file: the forbid is safe to add anywhere (it only takes power away); the permits give an assistant
# new reach, so they come with the office-role policies.
NEW_FORBID = "forbid-campaign-reply"
NEW_PERMITS = ["research-tools", "email-send-list-office", "social-reply-official", "report-save"]
ROLE = ["role-bls-series", "role-case-read", "role-census-query", "role-datagov-search", "role-draft-revise", "role-eia-query",
        "role-email-send-list", "role-email-send", "role-epa-search", "role-fcc-search", "role-fec-search",
        "role-inbox-read", "role-nasa-search", "role-noaa-climate", "role-nps-parks", "role-openfda-search",
        "role-report-save", "role-sam-entity-lookup", "role-social-post", "role-social-reply",
        "role-usaspending-search", "role-web-scrape"]
ALL = sorted(STARTER + [NEW_FORBID, *NEW_PERMITS] + ROLE)

# Stand-in for the aws CLI: an existing table and store, policies by description, STAFFER items from a file.
FAKE_AWS = f"""#!{sys.executable}
import json, os, re, sys
state_path = os.environ["FAKE_AWS_STATE"]
state = json.load(open(state_path))
args = sys.argv[1:]
state["calls"].append(args[:2])
op = args[1]
if op == "describe-table":
    pass
elif op == "list-policy-stores":
    print("ps-1")
elif op == "list-policies":
    name = re.search(r"description=='([^']+)'", args[args.index("--query") + 1]).group(1)
    print(state["policies"].get(name, "None"))
elif op == "create-policy":
    name = json.loads(args[args.index("--definition") + 1])["static"]["description"]
    state["policies"][name] = "avp-" + name
    print(state["policies"][name])
elif op == "scan":
    print(json.dumps({{"Items": state["staffers"]}}))
else:
    sys.exit("fake aws: unexpected call " + " ".join(args))
json.dump(state, open(state_path, "w"))
"""


def _staffer(name, role=None):
    item = {"pk": {"S": f"STAFFER#{name}"}}
    if role:
        item["officeRole"] = {"S": role}
    return item


@pytest.fixture
def fake(tmp_path):
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    (bin_dir / "aws").write_text(FAKE_AWS)
    (bin_dir / "aws").chmod(0o755)
    state_path = tmp_path / "state.json"
    env = {k: v for k, v in os.environ.items() if not k.startswith("AWS_")}
    env_file = tmp_path / ".env.aws"
    env.update(PATH=f"{bin_dir}:{os.environ['PATH']}", AWS_CONFIG_FILE="/dev/null",
               AWS_SHARED_CREDENTIALS_FILE="/dev/null", FAKE_AWS_STATE=str(state_path), PYTHON=sys.executable,
               MANDATE_ENV_FILE=str(env_file))

    class Fake:
        def setup(self, policies, staffers):
            state_path.write_text(json.dumps({"calls": [], "policies": policies, "staffers": staffers}))

        def run(self, *args):
            return subprocess.run(["bash", str(SCRIPT), *args], env=env, capture_output=True, text=True)

        def state(self):
            return json.loads(state_path.read_text())

        def env_names(self):
            line = next(l for l in env_file.read_text().splitlines() if l.startswith("AVP_POLICY_NAMES="))
            return sorted(json.loads(line.split("=", 1)[1].strip("'")).values())

    return Fake()


EXISTING = {name: f"avp-{name}" for name in STARTER if name != "draft-revise"}  # a store from before draft-revise
GOOD = [_staffer("maya", "caseworker"), _staffer("chiefofstaff", "chief_of_staff")]


def test_the_contracts_ship_the_expected_policy_files():
    names = sorted(p.stem for p in (ROOT / "packages" / "mandate-contracts" / "policies").glob("*.cedar"))
    assert len(names) == 34
    assert names == ALL


def test_without_the_flag_no_role_policy_is_added(fake):
    fake.setup(dict(EXISTING), GOOD)
    result = fake.run("--apply")
    assert result.returncode == 0, result.stderr
    # STARTER already holds draft-revise; the new forbid is created; the 4 new permits wait for the flag, like the role forbids.
    assert sorted(fake.state()["policies"]) == sorted([*STARTER, NEW_FORBID])
    assert f"created: {NEW_FORBID}" in result.stdout
    assert "created: draft-revise" in result.stdout and "skipped: draft-revise" not in result.stdout
    for name in NEW_PERMITS:
        assert f"skipped: {name}" in result.stdout, name
        assert f"created: {name}" not in result.stdout
    assert ["dynamodb", "scan"] not in fake.state()["calls"]
    assert result.stdout.count("skipped: role-") == 22


def test_with_the_flag_and_every_role_on_file_all_34_policies_exist_once(fake):
    fake.setup(dict(EXISTING), GOOD)
    assert fake.run("--apply", "--with-role-policies").returncode == 0
    assert sorted(fake.state()["policies"]) == ALL and len(ALL) == 34
    assert fake.env_names() == ALL
    again = fake.run("--apply", "--with-role-policies")  # idempotent
    assert again.returncode == 0 and "created:" not in again.stdout


@pytest.mark.parametrize("bad", [_staffer("bronco"), _staffer("aws", "senator")])
def test_a_staffer_without_a_valid_role_stops_before_any_role_policy(fake, bad):
    fake.setup(dict(EXISTING), [*GOOD, bad])
    result = fake.run("--apply", "--with-role-policies")
    assert result.returncode != 0
    assert bad["pk"]["S"].removeprefix("STAFFER#") in result.stderr
    assert "test-users.sh --apply" in result.stderr
    assert sorted(fake.state()["policies"]) == sorted(EXISTING)  # nothing added
    assert ["verifiedpermissions", "create-policy"] not in fake.state()["calls"]


def test_role_policies_already_in_the_store_stay_named_without_the_flag(fake):
    fake.setup({**EXISTING, **{name: f"avp-{name}" for name in ROLE}}, GOOD)
    assert fake.run("--apply").returncode == 0
    assert fake.env_names() == sorted([*STARTER, NEW_FORBID, *ROLE])


def test_the_default_run_is_a_dry_run(fake):
    fake.setup({}, [])
    result = fake.run()
    assert result.returncode == 0
    assert fake.state()["calls"] == []
    assert fake.state()["policies"] == {}


def test_dry_run_calls_nothing(fake):
    fake.setup({}, [])
    result = fake.run("--with-role-policies")
    assert result.returncode == 0
    assert fake.state()["calls"] == []
    assert "dynamodb scan" in result.stderr
