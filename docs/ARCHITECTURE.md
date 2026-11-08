# HomeFront Architecture

> Status: **Draft template**. Sections marked `TODO` still need decisions from the team.
> Last updated: 2026-11-07 (moved from Supabase to Amazon Cognito + DynamoDB)

## 1. Overview

**What HomeFront does:** TODO, one or two sentences on the product and who uses it.

**Core user flows:**
1. A user signs up (confirming their email with a code) or signs in.
2. A user views and edits their profile.
3. A user creates agents, grants them MCP tools, and runs them on Claude (Amazon Bedrock).
4. An operator authenticates an AI agent, allows selected MCP tools, revokes mid-task, and sees MITRE ATLAS-tagged denials.

## 2. Tech Stack

| Layer          | Choice                                   | Notes                                              |
|----------------|------------------------------------------|----------------------------------------------------|
| Frontend       | React 18 + Vite (`Frontend/`)            | Amplify Auth (`aws-amplify/auth`) for sign-in; all API calls via `src/lib/api.js` |
| Backend API    | FastAPI (Python)                         | Single service. Flask dropped (see §9)             |
| Auth           | Amazon Cognito user pool                 | Issues JWTs. The backend never handles passwords   |
| Database       | Amazon DynamoDB (on-demand)              | Only the backend's IAM role can reach the tables   |
| Agent model    | Claude on Amazon Bedrock                 | See README "Running agents"                        |
| Infrastructure | CloudFormation (`infra/cloudformation/homefront.yaml`) | Deployed with `infra/deploy.sh`       |
| File storage   | TODO (S3)                                | Avatars, uploads                                   |
| Hosting        | TODO (AWS: App Runner / ECS / Lambda)    | AWS x A10 hackathon                                |
| Secrets        | `.env` locally, TODO in prod (AWS Secrets Manager) | Never commit `.env`                      |

## 3. System Diagram

```mermaid
flowchart LR
    U[User / Browser] -->|sign up / sign in, SRP| C[Cognito user pool]
    C -->|ID + access + refresh tokens| U
    U -->|API request + Bearer ID token| API[FastAPI backend]
    API -->|verify signature via pool JWKS| C
    API -->|IAM role, keyed by token sub| DB[(DynamoDB)]
    API --> BR[Claude on Bedrock]
    API --> EXT[MCP tools / api.data.gov / email / social]
    API --> ATLAS[Vendored MITRE ATLAS pack]
```

## 4. Components

### 4.1 Frontend
- Handles sign-up, email-code confirmation, sign-in and sign-out through Amplify Auth (`src/lib/AuthProvider.jsx`).
- Amplify stores the tokens and refreshes them; `getIdToken()` always returns a current one.
- Sends `Authorization: Bearer <id_token>` with every backend request.
- Holds **only** the user pool id and app client id. Both are public; the client has no secret.

### 4.2 Amazon Cognito
- Source of truth for identities. The user's id everywhere is the token's `sub`.
- Sign-in with email + password (SRP, so the password never leaves the browser). Email confirmation required, by code.
- Password policy: 8+ characters with a lowercase letter and a number.
- Emails go through Cognito's built-in sender (a few per day). TODO: switch to SES before real users.
- Enabled providers: email/password. TODO: Google, GitHub (Cognito federation)?

### 4.3 FastAPI Backend
- Stateless. Reads the user's identity from the JWT on every request (`app/core/auth.py`).
- Auth dependency checks the RS256 signature against the pool's JWKS, the issuer (this pool), expiry, `token_use`, and the app client (`aud` for ID tokens, `client_id` for access tokens). Then exposes `user_id = sub`.
- Every DynamoDB read and write is keyed by that verified `sub` (profiles) or checked against the agent's `owner_id` (agents). This replaces Supabase RLS.

### 4.4 Database (DynamoDB)
- Three tables, named `<DYNAMODB_TABLE_PREFIX><name>`: `profiles`, `usernames`, `agents` (§6).
- Key schemas are defined once in `Backend/app/core/dynamodb.py` and mirrored in the CloudFormation template; `Backend/tests/test_infra.py` fails if they drift.
- Browsers never talk to DynamoDB. The `BackendDataPolicy` IAM policy (stack output) grants the backend access to these three tables only.
- Local development: DynamoDB Local via `scripts/run-dynamodb-local.sh`.

## 5. Authentication Flow

```mermaid
sequenceDiagram
    participant B as Browser
    participant C as Cognito
    participant A as FastAPI
    participant D as DynamoDB
    B->>C: signUp(email, password, name)
    C-->>B: code emailed
    B->>C: confirmSignUp(code), then sign in (SRP)
    C-->>B: ID token + access token + refresh token
    B->>A: GET /me (Bearer ID token)
    A->>A: verify JWT (JWKS, iss, exp, token_use, client)
    A->>D: get profile id = sub (create on first request)
    D-->>A: profile item
    A-->>B: 200 profile JSON
    Note over B,C: Amplify refreshes expired tokens automatically
```

**Token rules**
- ID and access tokens last 60 minutes; refresh tokens 30 days (set in the template).
- The backend rejects missing, expired or invalid tokens with `401`, and returns `503` if Cognito isn't configured.
- Authorization (roles/permissions): TODO. Options are a `role` attribute on profiles or Cognito groups (`cognito:groups` claim).

## 6. Data Model

### `profiles` (pk `id`)
| Attribute    | Type   | Notes                                                    |
|--------------|--------|----------------------------------------------------------|
| id           | S      | Cognito `sub`                                            |
| username     | S/NULL | Unique (reserved in `usernames`), 3–32 chars             |
| full_name    | S/NULL | From the Cognito `name` attribute at first sign-in       |
| avatar_url   | S/NULL |                                                          |
| role         |        | TODO: default `user`?                                    |
| created_at / updated_at | S | ISO 8601 UTC                                    |

- Created by the first authenticated `GET /me` or `PATCH /me` (conditional put, so two first requests can't clobber each other). This replaces the Postgres signup trigger.
- Users can only read and update their own profile: the API keys every call by the token's `sub`.

### `usernames` (pk `username`)
Reservation rows `{username, user_id}`. Setting a username writes the reservation, releases the old one and updates the profile in one `TransactWriteItems`. A taken name returns `409`. Usernames are case-sensitive, as before.

### `agents` (pk `id`)
User-defined agents. Read and written by the gateway (`AGENT_STORE=dynamodb`). Reads are strongly consistent, so a revoke applies to the very next tool call.

| Attribute     | Type    | Notes                                                       |
|---------------|---------|-------------------------------------------------------------|
| id            | S       | `^[a-z0-9][a-z0-9-]{2,63}$` (used in URLs)                  |
| owner_id      | S/NULL  | Cognito `sub`. null = shared demo agent                     |
| kind          | S       | `email` / `social` / `research` / `custom`. Fixed at creation; missing = `custom` |
| name / agency | S       | 1–80 / 1–120 chars                                          |
| instructions  | S       | ≤ 8000 chars. The agent's purpose and system prompt         |
| model         | S/NULL  | null = runner default                                       |
| max_steps     | N       | 1–50, default 10. Caps turns per run (AML.T0034)            |
| tool_grants   | L       | Tool names, checked against `TOOL_REGISTRY` by the API      |
| token_version | N       | Bumped on grant changes to invalidate workload tokens       |
| active        | BOOL    | Inactive agents can't get tokens or run                     |
| created_at / updated_at | S | ISO 8601 UTC                                      |

**Agent types** (`packages/identity-gateway/src/identity_gateway/agent_types.py`, public catalog at `GET /agent-types`): each kind supplies default instructions, tools and `max_steps` for fields left out of `POST /agents`, plus a tool ceiling. Email: `email.send`, `datagov.search`. Social: `social.post`, `social.reply`. Research: `web.scrape`, `datagov.search`, never an outbound tool, because it reads untrusted content. `custom` has no ceiling. Granting a tool outside the ceiling returns `403 tool_not_allowed_for_kind` and is audited as `grant_denied`. `/mcp/call` checks the ceiling again, so a grant written straight to the table is still blocked.

The field limits are enforced by the API's request models (DynamoDB has no check constraints). The demo agent `agent-comms-01` is seeded at startup if missing.

**API access** (`/agents*`, `/auth/token`, `/audit`): a signed-in user gets their own agents plus ownerless demo agents, and new agents are owned by the creator. Anonymous callers (only when `AGENT_ADMIN_REQUIRES_LOGIN=false`) get ownerless agents only. Other users' agents return `404`. An invalid user token is always `401`.

Workload tokens, agent runs and the audit log are still in memory.

### Domain tables
TODO: list HomeFront's own tables. Add each to `TABLES` in `Backend/app/core/dynamodb.py` **and** the CloudFormation template (plus the IAM policy). Every item that belongs to a user should carry the owner's `sub`, and every access should check it.

## 7. API Surface (v1)

| Method | Path             | Auth     | Purpose                         |
|--------|------------------|----------|---------------------------------|
| GET    | `/health`        | none     | Liveness check                  |
| GET    | `/me`            | required | Current user's profile (created on first call) |
| PATCH  | `/me`            | required | Update own profile (`409` if username taken) |
| GET    | `/profiles/{id}` | TODO     | Public profile lookup (if any)  |
| *      | `/agents...`     | see §6   | Agents, grants, runs            |

Conventions: JSON only, Pydantic models for requests and responses, errors shaped as `{"detail": "..."}`, prefix routes with `/api/v1`.

## 8. Project Structure

```
HomeFront/
├── Backend/
│   ├── app/
│   │   ├── main.py            # app factory, CORS, routers
│   │   ├── agent_iam.py       # mounts the agent gateway, wires DynamoDB + Bedrock
│   │   ├── core/
│   │   │   ├── config.py      # env settings
│   │   │   ├── auth.py        # Cognito JWT verification dependency
│   │   │   └── dynamodb.py    # table definitions + boto3 resource
│   │   ├── routers/           # profiles.py, health.py, <domain>.py
│   │   ├── schemas/           # Pydantic models
│   │   └── services/          # business logic (profiles.py)
│   ├── tests/
│   ├── requirements.txt
│   └── .env.example
├── infra/
│   ├── cloudformation/homefront.yaml   # Cognito, DynamoDB, IAM policy
│   └── deploy.sh
├── packages/identity-gateway/ # agent identity, grants, runner (dynamo_repo.py)
├── Frontend/
├── docs/
│   └── ARCHITECTURE.md
└── README.md
```

## 9. Key Decisions

| # | Decision                                   | Reason                                                       |
|---|--------------------------------------------|--------------------------------------------------------------|
| 1 | Cognito instead of custom auth             | Managed passwords and email verification; AWS-native for the hackathon |
| 2 | FastAPI only (no Flask)                    | One auth layer and one deploy. Async and Pydantic built in   |
| 3 | Profiles keyed by Cognito `sub`, separate from Cognito attributes | App data stays in our table; Cognito stays the identity source |
| 4 | Only the backend touches DynamoDB, scoped by IAM | The API is the single enforcement point; IAM limits the blast radius |
| 5 | Infrastructure only through the CloudFormation template | Every teammate's stack matches                        |
| 6 | Moved from Supabase (2026-11-07), fresh start | No production users yet; Cognito can't import Supabase password hashes |

## 10. Configuration

| Variable                    | Used by           | Secret? |
|-----------------------------|-------------------|---------|
| `COGNITO_USER_POOL_ID`, `VITE_COGNITO_USER_POOL_ID` | backend, frontend | no |
| `COGNITO_APP_CLIENT_ID`, `VITE_COGNITO_CLIENT_ID`   | backend, frontend | no (public client) |
| `AWS_REGION`                | backend           | no      |
| AWS credentials (`AWS_PROFILE`, keys, or a role) | backend | **yes** |
| `DYNAMODB_TABLE_PREFIX`     | backend           | no      |
| `DYNAMODB_ENDPOINT_URL`     | backend (DynamoDB Local only) | no |
| `AGENT_STORE`               | backend (`memory` / `dynamodb`) | no |
| `ALLOWED_ORIGINS`           | backend (CORS)    | no      |

## 11. Security Checklist
- [ ] `.env` is in `.gitignore`
- [ ] AWS credentials never reach the frontend or logs
- [ ] Backend role has only `BackendDataPolicy` (plus Bedrock) on DynamoDB
- [ ] Every user-owned item is keyed by or checked against the token's `sub`
- [ ] CORS limited to known frontend origins
- [ ] JWT checks cover issuer, client, `token_use` and expiry, not just signature
- [ ] Cognito email via SES before real users
- [ ] Rate limiting on public endpoints (TODO)

## 12. Open Questions
- TODO: Which federated providers (Google, GitHub)?
- TODO: Do we need roles or admin users (Cognito groups)?
- TODO: Where in AWS do we deploy the backend?
- TODO: Are profiles public or private?
