"""Print one Verified Permissions IsAuthorized request (AWS CLI input JSON) for a test case.

Built with the same `mandate_contracts.cedar` helpers the gateway uses, so the
policy tests send exactly the request shape the gateway sends.

Usage: python Backend/policy/request.py <case> <policy-store-id>
"""

import json
import sys

from mandate_contracts import cedar

AGENT = ("agent-1", "rep-x", "official")  # id, office, mode

# case -> (tool, resource entity, context overrides, expected decision)
CASES = {
    "own-office-case": ("case_read", cedar.case_entity("case-1", "rep-x"), {"delegatorActive": True}, "ALLOW"),
    "other-office-case": ("case_read", cedar.case_entity("case-21", "rep-y"), {"delegatorActive": True}, "DENY"),
    "delegator-inactive": ("inbox_read", cedar.tool_entity("inbox_read"), {"delegatorActive": False}, "DENY"),
    "bulk-export": ("records_bulk_export", cedar.tool_entity("records_bulk_export"), {"delegatorActive": True}, "DENY"),
    "email-unknown-recipient": (
        "email_send",
        cedar.tool_entity("email_send"),
        {"delegatorActive": True, "recipientKnown": False},
        "DENY",
    ),
    "campaign-post": (
        "social_post",
        cedar.tool_entity("social_post"),
        {"delegatorActive": True, "account": "campaign"},
        "DENY",
    ),
}


def request(case: str, store_id: str) -> dict:
    tool, resource, context, _ = CASES[case]
    agent_id, office, mode = AGENT
    return {
        "policyStoreId": store_id,
        "principal": {"entityType": "Mandate::Agent", "entityId": agent_id},
        "action": cedar.action(tool),
        "resource": resource["identifier"],
        "context": cedar.build_context(**context),
        "entities": {"entityList": [cedar.agent_entity(agent_id, office, mode), resource]},
    }


if __name__ == "__main__":
    if sys.argv[1] == "--list":
        for name, case in CASES.items():
            print(name, case[3])
    else:
        print(json.dumps(request(sys.argv[1], sys.argv[2])))
