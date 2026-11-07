# Frontend

TODO: choose a framework (see docs/ARCHITECTURE.md §12).

Requirements:
- Auth goes through `supabase-js` (sign up, log in, OAuth, refresh).
- Send `Authorization: Bearer <access_token>` with every backend request.
- Only `SUPABASE_URL` and `SUPABASE_ANON_KEY` belong here. Never the service role key.
