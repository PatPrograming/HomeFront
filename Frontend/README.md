# Frontend

React 18 + Vite. This is the main user-facing app.

```bash
# From repo root — single shared env file
cp .env.example .env     # fill SUPABASE_URL and SUPABASE_ANON_KEY (and DATA_GOV_API_KEY for the API)
./scripts/run-gateway.sh
./scripts/run-frontend.sh   # http://localhost:3000, proxies /api -> :8080
```

`vite.config.js` sets `envDir` to the repo root and maps `SUPABASE_URL` / `SUPABASE_ANON_KEY` into `import.meta.env.VITE_*` so you do not need a separate `Frontend/.env`.

## Routes

| Path | Auth | Purpose |
|------|------|---------|
| `/` | public | Home / hero |
| `/login` | public | Sign in / sign up (Supabase) |
| `/account` | required | Profile smoke test (`/api/v1/me`) |
| `/monitoring` | required | Agent IAM console (Prompt 1) |

## How it talks to the backend

- `src/lib/supabase.js`: Supabase client (sign up, log in, refresh). Public URL + anon key only.
- `src/lib/api.js`: calls `/api/v1/*`. User routes attach the Supabase access token.
- Agent MCP calls use a separate agent token from `POST /api/v1/auth/token` via `api.callTool(agentToken, …)`.

Never put the Supabase service role key or api.data.gov key in any browser-exposed `VITE_*` variable.
