"""Print one Verified Permissions IsAuthorized request (AWS CLI input JSON) for a test case.

Built with the same `mandate_contracts.cedar` helpers the gateway uses, so the
policy tests send exactly the request shape the gateway sends.

Usage: python Backend/policy/request.py <case> <policy-store-id>
"""

import json
import sys

from mandate_contracts import cedar

AGENT = ("agent-1", "rep-x", "official")  # id, office, mode
ACTIVE = {"delegatorActive": True}
OFFICIAL = {"delegatorActive": True, "account": "official"}
KNOWN = {"delegatorActive": True, "recipientKnown": True}

# case -> (office role, tool, resource entity, context overrides, expected decision)
CASES = {
    "own-office-case": ("caseworker", "case_read", cedar.case_entity("case-1", "rep-x"), ACTIVE, "ALLOW"),
    "other-office-case": ("caseworker", "case_read", cedar.case_entity("case-21", "rep-y"), ACTIVE, "DENY"),
    "delegator-inactive": ("caseworker", "inbox_read", cedar.tool_entity("inbox_read"), {"delegatorActive": False}, "DENY"),
    "bulk-export": ("chief_of_staff", "records_bulk_export", cedar.tool_entity("records_bulk_export"), ACTIVE, "DENY"),
    "email-unknown-recipient": (
        "caseworker",
        "email_send",
        cedar.tool_entity("email_send"),
        {"delegatorActive": True, "recipientKnown": False},
        "DENY",
    ),
    "campaign-post": (
        "chief_of_staff",
        "social_post",
        cedar.tool_entity("social_post"),
        {"delegatorActive": True, "account": "campaign"},
        "DENY",
    ),
    # Office roles (docs/ROLES.md): one allow and one deny each, plus an unknown role.
    "chief-of-staff-posts": ("chief_of_staff", "social_post", cedar.tool_entity("social_post"), OFFICIAL, "ALLOW"),
    "caseworker-emails": ("caseworker", "email_send", cedar.tool_entity("email_send"), KNOWN, "ALLOW"),
    "caseworker-cannot-post": ("caseworker", "social_post", cedar.tool_entity("social_post"), OFFICIAL, "DENY"),
    "comms-posts": ("communications_director", "social_post", cedar.tool_entity("social_post"), OFFICIAL, "ALLOW"),
    "comms-cannot-read-cases": ("communications_director", "case_read", cedar.case_entity("case-1", "rep-x"), ACTIVE, "DENY"),
    "aide-searches": ("legislative_aide", "datagov_search", cedar.tool_entity("datagov_search"), ACTIVE, "ALLOW"),
    "aide-cannot-email": ("legislative_aide", "email_send", cedar.tool_entity("email_send"), KNOWN, "DENY"),
    "intern-reads-web": ("intern", "web_scrape", cedar.tool_entity("web_scrape"), ACTIVE, "ALLOW"),
    "intern-cannot-read-inbox": ("intern", "inbox_read", cedar.tool_entity("inbox_read"), ACTIVE, "DENY"),
    "unknown-role": ("senator", "datagov_search", cedar.tool_entity("datagov_search"), ACTIVE, "DENY"),
}


def request(case: str, store_id: str) -> dict:
    role, tool, resource, context, _ = CASES[case]
    agent_id, office, mode = AGENT
    return {
        "policyStoreId": store_id,
        "principal": {"entityType": "Mandate::Agent", "entityId": agent_id},
        "action": cedar.action(tool),
        "resource": resource["identifier"],
        "context": cedar.build_context(**context),
        "entities": {"entityList": [cedar.agent_entity(agent_id, office, mode, role), resource]},
    }


if __name__ == "__main__":
    if sys.argv[1] == "--list":
        for name, case in CASES.items():
            print(name, case[4])
    else:
        print(json.dumps(request(sys.argv[1], sys.argv[2])))
