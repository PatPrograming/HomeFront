"""Backend/infra/test-users.sh against a fake `aws` executable: no AWS credentials, no network."""

import json
import os
import stat
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
SCRIPT = ROOT / "Backend" / "infra" / "test-users.sh"
POOL = "us-east-1_MLfQpodxG"
EXPECTED_GROUPS = {"bronco": "staffer", "scuawm": "staffer", "a10": "staffer", "aws": "staffer", "admin": "admin"}

# A stand-in for the aws CLI. It keeps Cognito users and DynamoDB items in a JSON state file and
# logs every call's argv, so the tests can see exactly what the script asked AWS to do.
FAKE_AWS = f"""#!{sys.executable}
import json, os, sys
state_path, log_path = os.environ["FAKE_AWS_STATE"], os.environ["FAKE_AWS_LOG"]
state = json.load(open(state_path)) if os.path.exists(state_path) else {{"users": {{}}, "items": {{}}}}
args = sys.argv[1:]
with open(log_path, "a") as log:
    log.write(json.dumps(args) + "\\n")
fail_on = os.environ.get("FAKE_AWS_FAIL_ON")
if fail_on and fail_on in args:
    sys.exit("An error occurred (InternalErrorException) when calling " + fail_on)

def opt(name):
    return args[args.index(name) + 1]

service, op = args[0], args[1]
users = state["users"]
if op == "list-users":
    name = opt("--filter").split('"')[1]
    print(users[name]["status"] if name in users else "None")
elif op == "admin-create-user":
    name = opt("--username")
    if name in users:
        sys.exit("An error occurred (UsernameExistsException)")
    users[name] = {{"status": "FORCE_CHANGE_PASSWORD", "groups": [], "password": None, "password_sets": 0}}
    print("{{}}")
elif op == "admin-set-user-password":
    body = json.load(open(opt("--cli-input-json").removeprefix("file://")))
    assert body["Permanent"] is True and body["UserPoolId"] == "{POOL}"
    user = users[body["Username"]]
    user.update(status="CONFIRMED", password=body["Password"], password_sets=user["password_sets"] + 1)
elif op == "admin-add-user-to-group":
    groups = users[opt("--username")]["groups"]
    if opt("--group-name") not in groups:
        groups.append(opt("--group-name"))
elif op == "put-item":
    item = json.loads(opt("--item"))
    state["items"][item["pk"]["S"] + "|" + item["sk"]["S"]] = item
else:
    sys.exit("fake aws: unexpected call " + " ".join(args))
json.dump(state, open(state_path, "w"))
"""


@pytest.fixture
def fake(tmp_path):
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    aws = bin_dir / "aws"
    aws.write_text(FAKE_AWS)
    aws.chmod(0o755)
    env = {k: v for k, v in os.environ.items() if not k.startswith("AWS_") and k != "COGNITO_USER_POOL_ID"}
    env.update(
        PATH=f"{bin_dir}:{os.environ['PATH']}",
        AWS_CONFIG_FILE="/dev/null",
        AWS_SHARED_CREDENTIALS_FILE="/dev/null",
        FAKE_AWS_STATE=str(tmp_path / "state.json"),
        FAKE_AWS_LOG=str(tmp_path / "calls.log"),
        TEST_USERS_FILE=str(tmp_path / "test-users.local.txt"),
    )

    class Fake:
        password_file = tmp_path / "test-users.local.txt"

        def run(self, *args, **extra_env):
            return subprocess.run(["bash", str(SCRIPT), *args], env={**env, **extra_env},
                                  capture_output=True, text=True)

        def calls(self):
            log = tmp_path / "calls.log"
            return [json.loads(line) for line in log.read_text().splitlines()] if log.exists() else []

        def state(self):
            return json.loads((tmp_path / "state.json").read_text())

        def passwords(self):
            return dict(line.split("\t") for line in self.password_file.read_text().splitlines())

    return Fake()


def _ops(calls, op):
    return [c for c in calls if c[1] == op]


def test_dry_run_calls_nothing(fake):
    result = fake.run()
    assert result.returncode == 0, result.stderr
    assert fake.calls() == []
    assert not fake.password_file.exists()
    assert "DRY RUN" in result.stdout
    for username in EXPECTED_GROUPS:
        assert f"STAFFER#{username}" in result.stdout


def test_apply_creates_each_user_once_with_group_and_staffer_item(fake):
    result = fake.run("--apply")
    assert result.returncode == 0, result.stderr
    calls = fake.calls()
    created = [c[c.index("--username") + 1] for c in _ops(calls, "admin-create-user")]
    assert sorted(created) == sorted(EXPECTED_GROUPS)
    assert all("--message-action" in c and c[c.index("--message-action") + 1] == "SUPPRESS"
               for c in _ops(calls, "admin-create-user"))
    assert all(c[c.index("--region") + 1] == "us-east-1" for c in calls)

    state = fake.state()
    for username, group in EXPECTED_GROUPS.items():
        user = state["users"][username]
        assert user["groups"] == [group]
        assert user["status"] == "CONFIRMED" and user["password_sets"] == 1
        item = state["items"][f"STAFFER#{username}|META"]
        assert item == {"pk": {"S": f"STAFFER#{username}"}, "sk": {"S": "META"}, "active": {"BOOL": True},
                        "office": {"S": "rep-x"}, "role": {"S": group}}


def test_passwords_go_only_to_the_private_file(fake):
    result = fake.run("--apply")
    assert result.returncode == 0, result.stderr
    assert stat.S_IMODE(fake.password_file.stat().st_mode) == 0o600
    passwords = fake.passwords()
    state = fake.state()
    assert set(passwords) == set(EXPECTED_GROUPS)
    for username, password in passwords.items():
        assert password == state["users"][username]["password"]  # the file holds the password Cognito got
        assert len(password) >= 16
        assert any(c.isupper() for c in password) and any(c.islower() for c in password)
        assert any(c.isdigit() for c in password) and any(not c.isalnum() for c in password)
        assert password not in result.stdout and password not in result.stderr
        assert not any(password in arg for call in fake.calls() for arg in call)  # never on a command line
    assert len(set(passwords.values())) == len(passwords)
    assert str(fake.password_file) in result.stdout


def test_rerun_is_idempotent_and_keeps_passwords(fake):
    assert fake.run("--apply").returncode == 0
    first_state, first_passwords = fake.state(), fake.passwords()
    calls_before = len(fake.calls())

    result = fake.run("--apply")
    assert result.returncode == 0, result.stderr
    second_calls = fake.calls()[calls_before:]
    assert _ops(second_calls, "admin-create-user") == []
    assert _ops(second_calls, "admin-set-user-password") == []
    assert fake.state() == first_state  # same users, groups, passwords and items
    assert fake.passwords() == first_passwords


def test_reset_passwords_changes_existing_passwords(fake):
    assert fake.run("--apply").returncode == 0
    old = fake.passwords()
    result = fake.run("--apply", "--reset-passwords")
    assert result.returncode == 0, result.stderr
    new = fake.passwords()
    state = fake.state()
    for username in EXPECTED_GROUPS:
        assert new[username] != old[username]
        assert state["users"][username]["password"] == new[username]
        assert state["users"][username]["password_sets"] == 2
        assert new[username] not in result.stdout + result.stderr


def test_stops_on_the_first_aws_error(fake):
    result = fake.run("--apply", FAKE_AWS_FAIL_ON="admin-add-user-to-group")
    assert result.returncode != 0
    assert "AWS call failed while adding bronco to group staffer" in result.stderr
    assert _ops(fake.calls(), "put-item") == []
    assert [c[c.index("--username") + 1] for c in _ops(fake.calls(), "admin-create-user")] == ["bronco"]
    # bronco's password was set before the failure, so it is kept for the next run.
    assert set(fake.passwords()) == {"bronco"}


def test_password_file_is_git_ignored():
    default = ROOT / "Backend" / "infra" / "test-users.local.txt"
    assert subprocess.run(["git", "-C", str(ROOT), "check-ignore", "-q", str(default)]).returncode == 0
