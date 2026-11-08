# AWS setup

## Gateway resources (`setup.sh`, `seed.py`)

`setup.sh` creates, in us-east-1, what the gateway needs. It reuses anything that already exists
and never deletes.

1. DynamoDB table `mandate` (`pk`/`sk` strings, on-demand).
2. A Verified Permissions policy store (description `mandate`, validation off).
3. The six policies from `packages/mandate-contracts/policies/*.cedar`; each policy's
   description is its readable name.
4. `Backend/infra/.env.aws` (untracked): `AVP_POLICY_STORE_ID` and `AVP_POLICY_NAMES`, the map
   from AVP's generated policy ids to the readable names that decision events show.

`seed.py` loads the staffers (maya: staffer, rep-x; chiefofstaff: admin, rep-x) and the cases
and emails from `agent/data/*.json`, or a small built-in fixture. Running it again resets maya to
active after an Offboard.

```bash
bash Backend/infra/setup.sh                    # dry run: print the commands only
bash Backend/infra/setup.sh --apply
set -a; source Backend/infra/.env.aws; set +a
.venv/bin/python Backend/infra/seed.py                  # dry run
.venv/bin/python Backend/infra/seed.py --apply
```
