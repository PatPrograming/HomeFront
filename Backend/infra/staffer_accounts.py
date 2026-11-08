"""Local helper for test-users.sh. It never calls AWS.

  staffer_accounts.py item <username> <office> <group> <office-role>
      Print the STAFFER#<username> / META item in DynamoDB JSON, built from mandate_contracts.
  staffer_accounts.py password-input <pool-id> <username> <path> [demo]
      Make a password, write the admin-set-user-password input JSON to <path> (mode 600)
      and print the password on stdout for the caller to capture (never to show).
      Random by default; with `demo`, the easy name-based demo password (proof of concept only).
"""

from __future__ import annotations

import json
import os
import secrets
import string
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "packages/mandate-contracts/src"))

from mandate_contracts.ddb import StafferItem, staffer_key  # noqa: E402

PASSWORD_LENGTH = 20
# Cognito-allowed symbols, minus quotes, backslash and spaces, so the password file stays easy to copy.
SYMBOLS = "!#%+-=@^_~"
CLASSES = (string.ascii_uppercase, string.ascii_lowercase, string.digits, SYMBOLS)


def ddb_item(username: str, office: str, group: str, office_role: str) -> dict:
    item = StafferItem(active=True, office=office, role=group, office_role=office_role)  # rejects an unknown role
    fields = {**staffer_key(username), **item.model_dump(mode="json", by_alias=True)}
    return {k: {"BOOL": v} if isinstance(v, bool) else {"S": v} for k, v in fields.items()}


def new_password() -> str:
    """One character from each class, the rest from all of them, shuffled."""
    alphabet = "".join(CLASSES)
    chars = [secrets.choice(c) for c in CLASSES]
    chars += [secrets.choice(alphabet) for _ in range(PASSWORD_LENGTH - len(chars))]
    secrets.SystemRandom().shuffle(chars)
    return "".join(chars)


def demo_password(username: str) -> str:
    """Proof of concept only: "bronco" -> "Bronco@2026". Meets the pool policy (upper, lower, digit,
    symbol, 8+); a name with no lowercase letter gets an "x" ("a10" -> "A10@2026x")."""
    password = f"{username[:1].upper()}{username[1:]}@2026"
    return password if any(c.islower() for c in password) else password + "x"


def write_password_input(pool_id: str, username: str, path: str, demo: bool = False) -> str:
    password = demo_password(username) if demo else new_password()
    body = {"UserPoolId": pool_id, "Username": username, "Password": password, "Permanent": True}
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "w") as f:
        json.dump(body, f)
    return password


def main(argv: list[str]) -> None:
    match argv:
        case ["item", username, office, group, office_role]:
            print(json.dumps(ddb_item(username, office, group, office_role)))
        case ["password-input", pool_id, username, path]:
            print(write_password_input(pool_id, username, path))
        case ["password-input", pool_id, username, path, "demo"]:
            print(write_password_input(pool_id, username, path, demo=True))
        case _:
            sys.exit(__doc__)


if __name__ == "__main__":
    main(sys.argv[1:])
