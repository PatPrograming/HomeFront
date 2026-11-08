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
POLICY_NAMES = sorted(p.stem for p in (ROOT / "packages" / "mandate-contracts" / "policies").glob("*.cedar"))
STARTER = [n for n in POLICY_NAMES if not n.startswith("role-")]
ROLE = [n for n in POLICY_NAMES if n.startswith("role-")]

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
elif op == "get-policy":
    pid = args[args.index("--policy-id") + 1]
    print(state["texts"].get(pid, "old text"))
elif op == "update-policy":
    pid = args[args.index("--policy-id") + 1]
    state["texts"][pid] = json.loads(args[args.index("--definition") + 1])["static"]["statement"]
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
        def setup(self, policies, staffers, texts=None):
            state = {"calls": [], "policies": policies, "staffers": staffers, "texts": texts or {}}
            state_path.write_text(json.dumps(state))

        def run(self, *args):
            return subprocess.run(["bash", str(SCRIPT), *args], env=env, capture_output=True, text=True)

        def state(self):
            return json.loads(state_path.read_text())

        def env_names(self):
            line = next(l for l in env_file.read_text().splitlines() if l.startswith("AVP_POLICY_NAMES="))
            return sorted(json.loads(line.split("=", 1)[1].strip("'")).values())

    return Fake()


EXISTING = {name: f"avp-{name}" for name in STARTER}
GOOD = [_staffer("maya", "caseworker"), _staffer("chiefofstaff", "chief_of_staff")]


def test_without_the_flag_no_role_policy_is_added(fake):
    fake.setup(dict(EXISTING), GOOD)
    result = fake.run("--apply")
    assert result.returncode == 0, result.stderr
    assert sorted(fake.state()["policies"]) == STARTER
    assert ["dynamodb", "scan"] not in fake.state()["calls"]
    assert result.stdout.count("skipped: role-") == len(ROLE)


def test_with_the_flag_and_every_role_on_file_the_six_role_policies_are_added_once(fake):
    fake.setup(dict(EXISTING), GOOD)
    assert fake.run("--apply", "--with-role-policies").returncode == 0
    assert sorted(fake.state()["policies"]) == sorted(STARTER + ROLE)
    assert fake.env_names() == sorted(STARTER + ROLE)
    again = fake.run("--apply", "--with-role-policies")  # idempotent
    assert again.returncode == 0 and "created:" not in again.stdout


@pytest.mark.parametrize("bad", [_staffer("bronco"), _staffer("aws", "senator")])
def test_a_staffer_without_a_valid_role_stops_before_any_role_policy(fake, bad):
    fake.setup(dict(EXISTING), [*GOOD, bad])
    result = fake.run("--apply", "--with-role-policies")
    assert result.returncode != 0
    assert bad["pk"]["S"].removeprefix("STAFFER#") in result.stderr
    assert "test-users.sh --apply" in result.stderr
    assert sorted(fake.state()["policies"]) == STARTER
    assert ["verifiedpermissions", "create-policy"] not in fake.state()["calls"]


def test_role_policies_already_in_the_store_stay_named_without_the_flag(fake):
    fake.setup({**EXISTING, **{name: f"avp-{name}" for name in ROLE}}, GOOD)
    assert fake.run("--apply").returncode == 0
    assert fake.env_names() == sorted(STARTER + ROLE)


def test_dry_run_calls_nothing(fake):
    fake.setup({}, [])
    result = fake.run("--with-role-policies")
    assert result.returncode == 0
    assert fake.state()["calls"] == []
    assert "dynamodb scan" in result.stderr


def _role_files():
    policies = ROOT / "packages" / "mandate-contracts" / "policies"
    return {name: (policies / f"{name}.cedar").read_text() for name in ROLE}


def test_update_rewrites_only_role_policies_whose_text_changed(fake):
    """The admin-editable role table needs the role-agnostic policies in AVP; setup.sh never updated before."""
    files = _role_files()
    current = {"avp-role-web-scrape": files["role-web-scrape"]}  # already the new text
    fake.setup({**EXISTING, **{name: f"avp-{name}" for name in ROLE}}, GOOD, current)
    result = fake.run("--apply", "--update-role-policies")
    assert result.returncode == 0, result.stderr
    assert result.stdout.count("updated: role-") == len(ROLE) - 1 and "exists: role-web-scrape" in result.stdout
    after = fake.state()
    assert after["texts"] == {f"avp-{n}": t for n, t in files.items()}
    assert ["verifiedpermissions", "create-policy"] not in after["calls"]
    assert fake.env_names() == sorted(STARTER + ROLE)  # same ids, same names
    again = fake.run("--apply", "--update-role-policies")
    assert "updated:" not in again.stdout


def test_without_the_update_flag_existing_role_policies_are_left_alone(fake):
    fake.setup({**EXISTING, **{name: f"avp-{name}" for name in ROLE}}, GOOD)
    assert fake.run("--apply", "--with-role-policies").returncode == 0
    assert ["verifiedpermissions", "update-policy"] not in fake.state()["calls"]
    assert ["verifiedpermissions", "get-policy"] not in fake.state()["calls"]


def test_update_dry_run_calls_nothing(fake):
    fake.setup({}, [])
    result = fake.run("--update-role-policies")
    assert result.returncode == 0 and fake.state()["calls"] == []
