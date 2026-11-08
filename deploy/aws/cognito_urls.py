"""Add (or remove) the CloudFront URL in the Cognito staff-web client's callback and logout URLs.
Dry run unless --apply. http://localhost:3000/ is always kept.

    python deploy/aws/cognito_urls.py https://dxxxx.cloudfront.net/ [--remove] [--apply]

update-user-pool-client resets every setting you leave out, so this copies the client's current
settings and changes only the two URL lists.
"""

from __future__ import annotations

import argparse
import os
import sys

REGION = "us-east-1"
USER_POOL_ID = os.environ.get("COGNITO_USER_POOL_ID", "us-east-1_MLfQpodxG")
STAFF_CLIENT_ID = os.environ.get("COGNITO_STAFF_CLIENT_ID", "5be15oitnpm14bg188jpqqs9qv")
LOCALHOST = "http://localhost:3000/"

# Fields describe-user-pool-client returns that update-user-pool-client accepts.
UPDATABLE = (
    "ClientName", "RefreshTokenValidity", "AccessTokenValidity", "IdTokenValidity", "TokenValidityUnits",
    "ReadAttributes", "WriteAttributes", "ExplicitAuthFlows", "SupportedIdentityProviders",
    "CallbackURLs", "LogoutURLs", "DefaultRedirectURI", "AllowedOAuthFlows", "AllowedOAuthScopes",
    "AllowedOAuthFlowsUserPoolClient", "AnalyticsConfiguration", "PreventUserExistenceErrors",
    "EnableTokenRevocation", "EnablePropagateAdditionalUserContextData", "AuthSessionValidity",
    "RefreshTokenRotation",
)


def normalized(url: str) -> str:
    if not url.startswith("https://"):
        raise SystemExit(f"expected an https:// URL, got {url!r}")
    return url.rstrip("/") + "/"


def with_url(urls: list[str], url: str, remove: bool) -> list[str]:
    kept = [u for u in urls if u != url]
    if not remove:
        kept.append(url)
    if LOCALHOST not in kept:
        kept.insert(0, LOCALHOST)
    return kept


def update_request(client: dict, url: str, remove: bool = False) -> dict:
    request = {key: client[key] for key in UPDATABLE if key in client}
    request["UserPoolId"] = client["UserPoolId"]
    request["ClientId"] = client["ClientId"]
    request["CallbackURLs"] = with_url(client.get("CallbackURLs", []), url, remove)
    request["LogoutURLs"] = with_url(client.get("LogoutURLs", []), url, remove)
    return request


def main(argv: list[str] | None = None, cognito=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("url", help="the console's HTTPS URL, e.g. https://dxxxx.cloudfront.net/")
    parser.add_argument("--remove", action="store_true", help="take the URL out instead (teardown)")
    parser.add_argument("--apply", action="store_true", help="update the client (default: dry run)")
    args = parser.parse_args(argv)
    url = normalized(args.url)

    if cognito is None:
        import boto3

        cognito = boto3.client("cognito-idp", region_name=REGION)
    client = cognito.describe_user_pool_client(UserPoolId=USER_POOL_ID, ClientId=STAFF_CLIENT_ID)["UserPoolClient"]
    request = update_request(client, url, args.remove)

    for field in ("CallbackURLs", "LogoutURLs"):
        print(f"{field}: {client.get(field, [])} -> {request[field]}")
    if not args.apply:
        print("DRY RUN: nothing changed. Re-run with --apply to update the staff-web client.")
        return 0
    cognito.update_user_pool_client(**request)
    print("updated the staff-web client.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
