"""DynamoDB tables of the teammates' alternative stack (infra/README.md).

Not used by the running app, which keeps everything in the `mandate` table
(packages/mandate-contracts `ddb`). Kept for scripts/create_tables.py and
tests/test_infra.py.

TABLES is the single source of truth for key schemas. The CloudFormation
template (infra/cloudformation/homefront.yaml) declares the same tables for
AWS; tests/test_infra.py checks the two stay in sync. `create_tables` builds
them in DynamoDB Local or a moto mock.

Tables (names are <DYNAMODB_TABLE_PREFIX><name>):
- profiles   pk id        one row per Cognito user (`sub`)
- usernames  pk username  reservation row; makes profile usernames unique
- agents     pk id        agent definitions (packages/identity-gateway/unported/dynamo_repo.py, not wired in)
"""
from __future__ import annotations

from functools import lru_cache
from typing import Any

import boto3

from app.core.config import get_settings

TABLES: dict[str, dict[str, Any]] = {
    "profiles": {
        "KeySchema": [{"AttributeName": "id", "KeyType": "HASH"}],
        "AttributeDefinitions": [{"AttributeName": "id", "AttributeType": "S"}],
    },
    "usernames": {
        "KeySchema": [{"AttributeName": "username", "KeyType": "HASH"}],
        "AttributeDefinitions": [{"AttributeName": "username", "AttributeType": "S"}],
    },
    "agents": {
        "KeySchema": [{"AttributeName": "id", "KeyType": "HASH"}],
        "AttributeDefinitions": [{"AttributeName": "id", "AttributeType": "S"}],
    },
}


def table_name(name: str) -> str:
    return f"{get_settings().dynamodb_table_prefix}{name}"


@lru_cache
def dynamodb_resource():
    settings = get_settings()
    kwargs: dict[str, Any] = {}
    if settings.resolved_aws_region:
        kwargs["region_name"] = settings.resolved_aws_region
    if settings.dynamodb_endpoint_url:
        kwargs["endpoint_url"] = settings.dynamodb_endpoint_url
    return boto3.resource("dynamodb", **kwargs)


def table(name: str):
    return dynamodb_resource().Table(table_name(name))


def create_tables(resource=None) -> list[str]:
    """Create any missing tables (local dev / tests). Returns names created."""
    resource = resource or dynamodb_resource()
    existing = set(resource.meta.client.list_tables()["TableNames"])
    created = []
    for name, spec in TABLES.items():
        full = table_name(name)
        if full in existing:
            continue
        resource.create_table(TableName=full, BillingMode="PAY_PER_REQUEST", **spec)
        resource.meta.client.get_waiter("table_exists").wait(TableName=full)
        created.append(full)
    return created
