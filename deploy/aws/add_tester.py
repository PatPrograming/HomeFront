"""Create a judge's or tester's account. Dry run unless --apply.

    python deploy/aws/add_tester.py <username> <email> [--office rep-x] [--admin] [--apply]

Three things, the same as the demo users get:
1. a Cognito user in the mandate pool; Cognito emails them a temporary password (nothing is printed),
2. membership in the `staffer` group (or `admin` with --admin),
3. an active STAFFER#<username> item in the `mandate` table (the office lives there, not in Cognito).
Offboarding them later is the console's Offboard button, as in the demo.
"""

from __future__ import annotations

import argparse
import os
import re
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO / "packages" / "mandate-contracts" / "src"))
sys.path.insert(0, str(REPO / "packages" / "identity-gateway" / "src"))

REGION = "us-east-1"
USER_POOL_ID = os.environ.get("COGNITO_USER_POOL_ID", "us-east-1_MLfQpodxG")


def main(argv: list[str] | None = None, cognito=None, store=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("username", help="lowercase sign-in name, e.g. judge1 (becomes the staffer id)")
    parser.add_argument("email", help="where Cognito sends the temporary password")
    parser.add_argument("--office", default="rep-x")
    parser.add_argument("--admin", action="store_true", help="admin group (can offboard) instead of staffer")
    parser.add_argument("--apply", action="store_true", help="create the account (default: dry run)")
    args = parser.parse_args(argv)
    if not re.fullmatch(r"[a-z0-9][a-z0-9._-]{1,31}", args.username):
        raise SystemExit("username: 2-32 chars, lowercase letters, digits, . _ -")
    if not re.fullmatch(r"[^@\s]+@[^@\s]+\.[^@\s]+", args.email):
        raise SystemExit("email doesn't look like an address")
    role = "admin" if args.admin else "staffer"

    print(f"user {args.username} <{args.email}> in group {role}, STAFFER#{args.username} office={args.office} active")
    if not args.apply:
        print("DRY RUN: nothing created. Re-run with --apply.")
        return 0

    from mandate_contracts.ddb import StafferItem

    if cognito is None:
        import boto3

        cognito = boto3.client("cognito-idp", region_name=REGION)
    if store is None:
        from identity_gateway.storage import MandateStore

        os.environ.setdefault("AWS_REGION", REGION)
        store = MandateStore.from_env()

    cognito.admin_create_user(
        UserPoolId=USER_POOL_ID, Username=args.username, DesiredDeliveryMediums=["EMAIL"],
        UserAttributes=[{"Name": "email", "Value": args.email}, {"Name": "email_verified", "Value": "true"}],
    )
    cognito.admin_add_user_to_group(UserPoolId=USER_POOL_ID, Username=args.username, GroupName=role)
    store.put_staffer(args.username, StafferItem(active=True, office=args.office, role=role))
    print(f"created. {args.email} gets a temporary password from Cognito; they set a new one at first sign-in.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
