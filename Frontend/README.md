# Frontend

React 18 + Vite. This is the main user-facing app.

```bash
# From repo root — single shared env file
cp .env.example .env     # fill COGNITO_* (from ./infra/deploy.sh) and DATA_GOV_API_KEY for the API
./scripts/run-gateway.sh
./scripts/run-frontend.sh   # http://localhost:3000, proxies /api -> :8080
```

`vite.config.js` sets `envDir` to the repo root and maps `COGNITO_USER_POOL_ID` / `COGNITO_APP_CLIENT_ID` into `import.meta.env.VITE_COGNITO_*` so you do not need a separate `Frontend/.env`.

## Routes

| Path | Auth | Purpose |
|------|------|---------|
| `/` | public | Home / hero |
| `/login` | public | Sign in / sign up (Cognito) |
| `/account` | required | Profile smoke test (`/api/v1/me`) |
| `/monitoring` | required | Agent IAM console (Prompt 1) |

## How it talks to the backend

- `src/lib/cognito.js`: Amplify Auth for the Cognito user pool. Public pool id + client id only.
- `src/lib/api.js`: calls `/api/v1/*`. User routes attach `Authorization: Bearer <id_token>` from the Cognito session.
- Agent MCP calls use a separate agent token from `POST /api/v1/auth/token` via `api.callTool(agentToken, …)`.
- `src/lib/AuthProvider.jsx` / `src/components/RequireAuth.jsx`: session state and protected routes.

## Adding a backend call

1. Add the endpoint in `Backend/app/routers/` (or agent IAM packages).
2. Add one method to `api` in `src/lib/api.js`.
3. Call it from a page. Wrap the route in `<RequireAuth>` if it needs a login.

Never put AWS credentials or `DATA_GOV_API_KEY` in any browser-exposed `VITE_*` variable.
