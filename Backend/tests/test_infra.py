"""Keep the CloudFormation template in sync with the tables the code expects."""
from pathlib import Path

import yaml

from app.core.dynamodb import TABLES

TEMPLATE = Path(__file__).resolve().parents[2] / "infra" / "cloudformation" / "homefront.yaml"


class _CfnLoader(yaml.SafeLoader):
    pass


def _tag(loader, suffix, node):
    if isinstance(node, yaml.ScalarNode):
        value = loader.construct_scalar(node)
    elif isinstance(node, yaml.SequenceNode):
        value = loader.construct_sequence(node)
    else:
        value = loader.construct_mapping(node)
    return {f"!{suffix}": value}


_CfnLoader.add_multi_constructor("!", _tag)


def _template() -> dict:
    return yaml.load(TEMPLATE.read_text(), Loader=_CfnLoader)


def test_tables_match_code():
    resources = _template()["Resources"]
    tables = {
        r["Properties"]["TableName"]["!Sub"].removeprefix("${Prefix}"): r["Properties"]
        for r in resources.values()
        if r["Type"] == "AWS::DynamoDB::Table"
    }
    assert set(tables) == set(TABLES)
    for name, spec in TABLES.items():
        assert tables[name]["KeySchema"] == spec["KeySchema"], name
        assert tables[name]["AttributeDefinitions"] == spec["AttributeDefinitions"], name
        assert tables[name]["BillingMode"] == "PAY_PER_REQUEST"


def test_backend_policy_covers_every_table_and_operation_used():
    resources = _template()["Resources"]
    [stmt] = resources["BackendDataPolicy"]["Properties"]["PolicyDocument"]["Statement"]
    table_resources = {k for k, r in resources.items() if r["Type"] == "AWS::DynamoDB::Table"}
    assert {g["!GetAtt"].split(".")[0] for g in stmt["Resource"]} == table_resources
    # Every DynamoDB call the backend makes (TransactWriteItems is authorized
    # per item as Put/Update/Delete/ConditionCheck).
    assert set(stmt["Action"]) == {
        "dynamodb:GetItem",
        "dynamodb:PutItem",
        "dynamodb:UpdateItem",
        "dynamodb:DeleteItem",
        "dynamodb:Scan",
        "dynamodb:Query",
        "dynamodb:ConditionCheckItem",
    }


def test_web_client_is_public_srp_client():
    client = _template()["Resources"]["WebClient"]["Properties"]
    assert client["GenerateSecret"] is False
    assert "ALLOW_USER_PASSWORD_AUTH" not in client["ExplicitAuthFlows"]
    assert "ALLOW_USER_SRP_AUTH" in client["ExplicitAuthFlows"]
