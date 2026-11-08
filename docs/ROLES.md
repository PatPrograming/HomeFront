# Office roles

Each HomeFront staffer has one office role. The role decides which tools their AI assistant may use, and Cedar
in Amazon Verified Permissions enforces it on every tool call. The console only displays the role.

## Where the role lives and who checks it

- The role is stored in DynamoDB as `officeRole` on the staffer's `STAFFER#<username>` / `META` item. Only
  `Backend/infra/test-users.sh` (admin credentials) and `seed.py` write it.
- On every tool call, the gateway reads the role fresh from that item and sends it to Cedar as `principal.role`.
  It never takes the role from the mandate token, the request body or tool input, so a role change applies on
  the very next call.
- Every role rule is a Cedar `forbid`: `forbid(action == X) unless { principal has role && [roles].contains(principal.role) }`.
  Cedar lets any forbid win, so:
  - a role can only take tools away; it never grants one the starter policies would refuse.
  - The starter forbids, `forbid-bulk-export` and `forbid-campaign-post`, still bind every role, the Chief of
    Staff included.
  - A missing, empty or unknown role matches no list, so Cedar denies every tool (fail closed).
- The Cognito group (`staffer` or `admin`) is separate. It only decides who sees the admin pages.

Code: `packages/mandate-contracts/src/mandate_contracts/roles.py`. Policies:
`packages/mandate-contracts/policies/role-*.cedar`.

## Role and permission table

The assistant and the Agents tab share this one table (`ROLE_TOOLS`). It covers all 21 governed tools. Each ❌ names the Cedar policy that blocks it.

| Tool | What it does | Chief of Staff | Caseworker | Communications Director | Legislative Aide | Intern |
|---|---|---|---|---|---|---|
| `inbox_read` | read the office inbox | ✅ | ✅ | ✅ | ✅ | ❌ `role-inbox-read` |
| `case_read` | open constituent case files | ✅ | ✅ | ❌ `role-case-read` | ❌ `role-case-read` | ❌ `role-case-read` |
| `datagov_search` | search public government data | ✅ | ✅ | ✅ | ✅ | ✅ |
| `web_scrape` | read public web pages | ✅ | ✅ | ✅ | ✅ | ✅ |
| `email_send` | send email for the office | ✅ | ✅ | ✅ | ❌ `role-email-send` | ❌ `role-email-send` |
| `social_post` | post on the office's official account | ✅ | ❌ `role-social-post` | ✅ | ❌ `role-social-post` | ❌ `role-social-post` |
| `email_send_list` | email the whole office mailing list (Chief of Staff only; always held for review) | ✅ | ❌ `role-email-send-list` | ❌ `role-email-send-list` | ❌ `role-email-send-list` | ❌ `role-email-send-list` |
| `social_reply` | reply to comments on the office's official account | ✅ | ❌ `role-social-reply` | ✅ | ❌ `role-social-reply` | ❌ `role-social-reply` |
| `usaspending_search` | search federal contract awards | ✅ | ❌ `role-usaspending-search` | ❌ `role-usaspending-search` | ✅ | ❌ `role-usaspending-search` |
| `sam_entity_lookup` | look up federal vendors in SAM.gov | ✅ | ❌ `role-sam-entity-lookup` | ❌ `role-sam-entity-lookup` | ✅ | ❌ `role-sam-entity-lookup` |
| `report_save` | save reports for the office | ✅ | ✅ | ✅ | ✅ | ❌ `role-report-save` |
| `census_query` | look up Census data | ✅ | ✅ | ✅ | ✅ | ✅ |
| `openfda_search` | search FDA drug, device and food data | ✅ | ✅ | ✅ | ✅ | ❌ `role-openfda-search` |
| `eia_query` | look up energy data | ✅ | ✅ | ✅ | ✅ | ❌ `role-eia-query` |
| `nasa_search` | search NASA images | ✅ | ✅ | ✅ | ✅ | ✅ |
| `noaa_climate` | look up NOAA climate data | ✅ | ✅ | ✅ | ✅ | ✅ |
| `nps_parks` | look up National Park Service parks | ✅ | ✅ | ✅ | ✅ | ✅ |
| `bls_series` | look up labor statistics | ✅ | ✅ | ✅ | ✅ | ❌ `role-bls-series` |
| `fec_search` | search campaign finance records | ✅ | ✅ | ✅ | ✅ | ❌ `role-fec-search` |
| `epa_search` | search EPA facility records | ✅ | ✅ | ✅ | ✅ | ❌ `role-epa-search` |
| `fcc_search` | search FCC licenses | ✅ | ✅ | ✅ | ✅ | ❌ `role-fcc-search` |
| `records_bulk_export` | export records in bulk | ❌ `forbid-bulk-export` | ❌ | ❌ | ❌ | ❌ |
| `social_post` to the campaign account | post on a campaign account | ❌ `forbid-campaign-post` | ❌ | ❌ | ❌ | ❌ |
| `social_reply` to the campaign account | reply on a campaign account | ❌ `forbid-campaign-reply` | ❌ | ❌ | ❌ | ❌ |

Every ✅ still passes through the starter permits: the staffer must be active, case files must belong to the same
office, email must go to a known recipient, and posts and replies must use the official account. Earned autonomy
also still holds outbound email and posts as drafts until the staffer allows that kind of message.

`email_send_list` is locked to the Chief of Staff (`LOCKED_TOOLS`). An admin who edits a role's tool list cannot
add it to any other role. Every mailing-list email is held for review, even with every grant and "auto" on.

## Agents tab: who can give an agent which tool

The "New agent" page shows a permission picker. These rules apply to the page and to the server. The server
enforces them on its own, so a direct API call gets no extra access.

- **Options.** The picker offers the agent kind's ceiling intersected with the creator's role. A tool outside
  either one is greyed out with the reason.
- **Recommended.** The picker pre-ticks the least privilege for the kind. `email.send` for email, `social.post`
  and `social.reply` for social, the type's default tools for research and market research, and nothing for
  custom. Recommended is also what an agent gets when the request leaves out `tool_grants`.
- **Strict names.** The server accepts only exact tool names from the registry. Unknown names, other casing,
  extra spaces and `email_send` instead of `email.send` get 400 `unknown_tools`. A name listed twice gets 400
  `duplicate_tools`.
- **Out of bounds.** A tool outside the kind ceiling or the creator's role gets 403 and an audit entry
  (`grant_denied`). The message names the exact tools. The same checks run on create and on every grant change.
- **Forged fields.** A body with `owner_id`, `office` or `user_id` gets 422.
- **At run time.** An agent can use a tool only if it is in the agent's stored grants and the owner's office role
  allows it right now. The server reads the role fresh from DynamoDB on every call and on approving a draft. A
  demoted or offboarded owner loses the tool on the next call (403 `role_not_allowed`).
- **Ownerless (shared) agents.** Only an admin can change their grants. A call is capped by the role of the
  staffer who started the run, or the staffer making the call. If neither is known, the call is denied.
- **Admin group.** The `admin` Cognito group never widens an office role. An admin with the Caseworker role
  gets the Caseworker's options.

## Assistant chat: connecting an account

If Gmail or X is not connected yet, an outbound call comes back as `needs_connection`, and the assistant tells
the staffer to connect that account first.

## Demo accounts

| Username | Cognito group | Office role |
|---|---|---|
| `admin` | `admin` | Chief of Staff |
| `chiefofstaff` | `admin` | Chief of Staff |
| `bronco` | `staffer` | Caseworker |
| `maya` | `staffer` | Caseworker |
| `scuacm` | `staffer` | Communications Director |
| `a10` | `staffer` | Legislative Aide |
| `aws` | `staffer` | Intern |

`scuacm` follows Huy's spelling in the request ("bronco, scuacm, a10, aws, admin"). Earlier messages and the first
version of `test-users.sh` said `scuawm`. That account was never created, so nothing needs renaming.

Since `maya` is a Caseworker, her assistant can no longer post on the official account. Use `scuacm` or
`chiefofstaff` to demo posting, and `maya` or `bronco` to show the same request being denied by role.

## Why these roles and these permissions

The roles follow the standard staff of a US congressional office. CRS describes the duties as follows:

- Caseworkers handle constituents' problems with federal agencies.
- Legislative correspondents draft replies to constituent mail.
- Communications directors and press secretaries run the website and social media.
- The chief of staff runs the office.

Source: CRS R46262, *Congressional Staff: Duties and Qualifications*.

The access choices come from these rules:

1. **Case files are for caseworkers.**
   - An agency generally cannot answer a congressional inquiry without a Privacy Act release signed by the
     constituent.
   - CRS suggests limiting casework correspondence to the staff assigned to constituent services, and names
     health, veterans, Medicare and immigration cases as sensitive.
   - So only the Caseworker and the Chief of Staff open case files. Source: CRS RL33209, *Casework in a
     Congressional Office*.
2. **Official resources are not for campaigns.**
   - House and Senate rules bar staff time, official email and official accounts from campaign use.
   - That is `forbid-campaign-post`, and it binds every role. Sources: House Ethics Manual, *General Prohibition
     Against Using Official Resources for Campaign or Political Purposes*; Senate Ethics Committee, *Campaign
     Activity*.
3. **Posting is the communications office's job.**
   - Official social accounts must relate to official duties, and the communications director owns them.
   - So only the Communications Director (and the Chief of Staff) post. Source: CRS R43477 on House social media
     rules.
4. **Legislative aides work on policy, not cases.**
   - They read issue mail and research.
   - Replies go out through review, so the role has no direct email or posting.
5. **Interns get public research only.**
   - No official source sets intern access to constituent records. CRS R44491 only says office technology is for
     official business, and offices set their own rules.
   - Least privilege means no inbox (constituents' personal data), no case files, and no sending.
   - This tier is our recommendation, not a rule.

The research found no official source that defines intern or legislative-aide access, and no state-legislature role
definitions. Those tiers are least-privilege choices made for the demo.

## Sources

- CRS RL33209, Casework in a Congressional Office: https://www.everycrsreport.com/reports/RL33209.html
- CRS R46262, Congressional Staff: Duties and Qualifications: https://www.everycrsreport.com/reports/R46262.html
- CRS R43477, Social Media in the House of Representatives: https://www.everycrsreport.com/reports/R43477.html
- CRS R44491, Internships in Congressional Offices: FAQ:
  https://www.everycrsreport.com/files/2026-04-29_R44491_2e9a9b3f28921c25a5db5d9f6075cd6d2ce94882.html
- House Ethics Manual, official resources and campaigns:
  https://ethics.house.gov/manual/general-prohibition-against-using-official-resources-for-campaign-or-political-purposes
- Senate Ethics Committee, Campaign Activity: https://ethics.senate.gov/public/index.cfm/campaign-activity

## Least privilege: what we guarantee

The assistant acts for one staffer and can never do more than that staffer's role allows, in any session or task.
Each guarantee below has a test that fails if it breaks. All tests run offline. The gateway tests use the real Cedar
engine (cedarpy) on the same policy files that go into Verified Permissions.

| # | Guarantee | Proved by |
|---|---|---|
| 1 | **The agent never gets more than its staffer's role.** This holds for every role, including no role, and for every tool. The test also sets every category to "auto" and grants every category for the session. The agent is allowed exactly the role's tools. Other offices' cases, unknown recipients, campaign posts and bulk export are refused for every role. | `test_the_agent_never_gets_a_tool_its_staffers_role_lacks` (`packages/identity-gateway/tests/test_gw_roles.py`), built from `ROLE_TOOLS`, `TOOLS` and the autonomy categories, so new roles, tools and categories are covered automatically |
| 2 | **Each role's permissions are exactly its row in the table above.** Cedar decides, and the Python test double agrees with Cedar everywhere. | `test_real_cedar_allows_exactly_the_roles_tool_list`, `test_starter_policy_authorizer_matches_real_cedar` |
| 3 | **A missing or unknown role gets nothing.** | `test_an_unknown_or_empty_role_is_denied_everything`, `test_an_agent_entity_with_no_role_attribute_is_denied_everything`, `test_a_staffer_with_no_role_on_file_is_denied` |
| 4 | **The bulk-export and campaign-post bans bind everyone, even with every grant.** | `test_forbids_still_win_for_the_chief_of_staff`, `test_forbids_win_over_every_grant_for_the_chief_of_staff` |
| 5 | **A session grant or "auto" setting can't exceed the role.** The API refuses one for a category whose tool the role lacks, and the gateway ignores any that exists anyway, because Cedar decides first. | `test_a_session_grant_outside_the_role_is_refused`, `test_auto_outside_the_role_is_refused` (`Backend/tests/test_least_privilege.py`), `test_a_grant_or_auto_outside_the_role_is_ignored_by_the_gateway` |
| 6 | **Grants belong to one console session.** A second session for the same staffer does not inherit them, and offboarding deletes them. | `test_grants_are_per_session_and_die_with_offboarding`, `test_a_grant_lets_the_agent_send_that_category_for_this_session_only`, `test_offboarding_wipes_grants_and_the_next_call_is_denied` |
| 7 | **Role, office and category come only from server data.** A client-sent role, office or category is rejected or ignored on sessions, chat messages (staffer and agent), grants, autonomy, draft approval and MCP tool input. | `test_client_sent_role_or_office_changes_nothing_on_any_route`, `test_a_role_sent_by_the_client_is_ignored`, `test_a_category_cannot_be_chosen_by_the_agent`, `test_the_role_comes_from_dynamodb_not_from_tool_input`, `test_role_and_office_in_tool_input_are_ignored` |
| 8 | **A role change or offboarding applies on the next call,** including to a draft approved later. | `test_a_role_change_in_dynamodb_applies_on_the_next_call`, `test_a_draft_approved_after_a_role_change_is_checked_against_the_new_role`, `test_a_replayed_mandate_after_offboarding_is_denied` |
| 9 | **The agent's identity can't be swapped.** A mandate for one staffer can't use another staffer's session, and can't claim another staffer on its own session. | `test_a_mandate_for_one_staffer_cannot_use_another_staffers_session`, `test_a_mandate_claiming_another_staffer_on_your_session_is_denied` |

What is per staffer rather than per session: the "auto" setting from earned autonomy is a standing choice the
staffer makes in the console. It applies to their later sessions too, by design. It still only skips the review
queue for a call Cedar has already allowed for the staffer's role (guarantees 1 and 5), and the API refuses it for
a category outside the role.

## Turn it on in AWS

The role policies and the new tool permits go into the shared policy store only on request, and only when every
staffer already has a role. Applied too early, they would deny every call for staffers whose role is not written
yet. Only Huy runs `--apply`.

Before you start: the new gateway code is running, and `bash Backend/infra/test-users.sh --apply` has written every
role, including `maya` and `chiefofstaff`.

1. Preview the changes. This prints the AWS commands and calls nothing.

   ```bash
   bash Backend/infra/setup.sh --with-role-policies
   ```

   Expect `created:` lines for the 5 new starter policies and the 15 new `role-*` policies, and `exists:` for the rest.

2. Apply them.

   ```bash
   bash Backend/infra/setup.sh --apply --with-role-policies
   ```

   It reads every STAFFER item, stops if any has no valid role, and otherwise creates only the missing policies.

3. Check the real store.

   ```bash
   bash Backend/policy/tests.sh --apply
   ```

   Every case should print `PASS`.

Without `--with-role-policies`, `setup.sh` adds no role policy and none of the new tool permits.
`forbid-campaign-reply` is always created, because a forbid cannot widen access.

## Check it

Offline (real Cedar engine, no AWS): `packages/identity-gateway/tests/test_gw_roles.py`. Against the real policy
store: `bash Backend/policy/tests.sh --apply`, which includes one ALLOW and one DENY per role.
