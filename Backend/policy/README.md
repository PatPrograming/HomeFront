# Policy tests

`tests.sh` asks the real Verified Permissions policy store six questions and checks each answer:

| Case | Expected | Policy that decides |
|---|---|---|
| Read a case from the agent's own office | ALLOW | `case-read-same-office` |
| Read a case from another office | DENY | no permit matches |
| Any tool while the delegator is inactive | DENY | every permit needs `delegatorActive` |
| Bulk export | DENY | `forbid-bulk-export` |
| Email an address not on file | DENY | `email-send-known-recipient` needs `recipientKnown` |
| Post to the campaign account | DENY | `forbid-campaign-post` |

`request.py` builds each request with the same `mandate_contracts.cedar` helpers the gateway
uses. The policies themselves are in `packages/mandate-contracts/policies/`.

```bash
bash Backend/policy/tests.sh --dry-run   # print the AWS commands only
bash Backend/policy/tests.sh             # needs Backend/infra/.env.aws from setup.sh and AWS credentials
```
