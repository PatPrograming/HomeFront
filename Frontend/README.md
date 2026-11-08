# Frontend

React 18 + Vite. This is the main user-facing app.

```bash
cp .env.example .env     # fill VITE_COGNITO_USER_POOL_ID and VITE_COGNITO_CLIENT_ID (from ./infra/deploy.sh)
npm install
npm run dev              # http://localhost:3000, proxies /api -> http://127.0.0.1:8080
```

Start the API first with `./scripts/run-gateway.sh`.

## How it talks to the backend

- `src/lib/cognito.js`: configures Amplify Auth for the Cognito user pool. Public pool id + client id only.
- `src/lib/api.js`: the only place that calls the backend. Every call goes to `/api/v1/*`
  and attaches `Authorization: Bearer <id_token>` from the current Cognito session.
- `src/lib/AuthProvider.jsx` / `src/components/RequireAuth.jsx`: session state (sign up, email code, sign in, sign out) and protected routes.

## Adding a backend call

1. Add the endpoint in `Backend/app/routers/`.
2. Add one method to `api` in `src/lib/api.js`.
3. Call it from a page. Wrap the route in `<RequireAuth>` if it needs a login.

Never put AWS credentials here. The browser talks only to Cognito and our API, never to DynamoDB.
