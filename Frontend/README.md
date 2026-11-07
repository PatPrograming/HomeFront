# Frontend

React 18 + Vite. This is the main user-facing app.

```bash
cp .env.example .env     # fill VITE_SUPABASE_URL and VITE_SUPABASE_ANON_KEY
npm install
npm run dev              # http://localhost:3000, proxies /api -> http://127.0.0.1:8080
```

Or from the repo root: `./scripts/run-frontend.sh`.

Start the API first with `./scripts/run-gateway.sh`.

## Routes

| Path | Auth | Purpose |
|------|------|---------|
| `/` | public | Home / hero |
| `/login` | public | Sign in / sign up (Supabase) |
| `/account` | required | Profile smoke test (`/api/v1/me`) |
| `/monitoring` | required | Agent IAM console (Prompt 1): mint agent token, MCP tools, revoke, ATLAS audit |

## How it talks to the backend

- `src/lib/supabase.js`: Supabase client (sign up, log in, refresh). Public URL + anon key only.
- `src/lib/api.js`: the only place that calls the backend. User routes attach `Authorization: Bearer <supabase access_token>`.
- Agent MCP calls use a **separate** agent token from `POST /api/v1/auth/token` via `api.callTool(agentToken, …)`.
- `src/lib/AuthProvider.jsx` / `src/components/RequireAuth.jsx`: session state and protected routes.

## Adding a backend call

1. Add the endpoint in `Backend/app/routers/` (or agent IAM packages).
2. Add one method to `api` in `src/lib/api.js`.
3. Call it from a page. Wrap the route in `<RequireAuth>` if it needs a login.

Never put the Supabase service role key here.
