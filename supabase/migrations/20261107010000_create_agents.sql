-- Agents: user-defined AI agents and their MCP tool grants.
-- Idempotent: safe to re-run.
--
-- The gateway reads and writes this table with the service role (it resolves
-- agent workload tokens without a user session). The RLS policies below are
-- for user-scoped access: a signed-in user can only see and change agents
-- they own. Rows with owner_id = null are system/demo agents that only the
-- service role can see.
create table if not exists public.agents (
  id             text primary key check (id ~ '^[a-z0-9][a-z0-9-]{2,63}$'),
  owner_id       uuid references auth.users(id) on delete cascade,
  name           text not null check (char_length(name) between 1 and 80),
  agency         text not null check (char_length(agency) between 1 and 120),
  -- The agent's purpose; used as its system prompt by the runner.
  instructions   text not null default '' check (char_length(instructions) <= 8000),
  -- null = use the runner's default model.
  model          text check (char_length(model) <= 100),
  -- Hard cap on model/tool turns per run (cost harvesting, AML.T0034).
  max_steps      integer not null default 10 check (max_steps between 1 and 50),
  -- Tool names are validated against the gateway's TOOL_REGISTRY by the API.
  tool_grants    text[] not null default '{}',
  -- Bumped on grant changes to invalidate outstanding workload tokens.
  token_version  integer not null default 1,
  active         boolean not null default true,
  created_at     timestamptz not null default now(),
  updated_at     timestamptz not null default now()
);

create index if not exists agents_owner_id_idx on public.agents (owner_id);

alter table public.agents enable row level security;

drop policy if exists "Users can read own agents" on public.agents;
create policy "Users can read own agents"
  on public.agents for select
  using ((select auth.uid()) = owner_id);

drop policy if exists "Users can create own agents" on public.agents;
create policy "Users can create own agents"
  on public.agents for insert
  with check ((select auth.uid()) = owner_id);

drop policy if exists "Users can update own agents" on public.agents;
create policy "Users can update own agents"
  on public.agents for update
  using ((select auth.uid()) = owner_id)
  with check ((select auth.uid()) = owner_id);

drop policy if exists "Users can delete own agents" on public.agents;
create policy "Users can delete own agents"
  on public.agents for delete
  using ((select auth.uid()) = owner_id);

-- Reuses public.set_updated_at() from the profiles migration.
drop trigger if exists agents_set_updated_at on public.agents;
create trigger agents_set_updated_at
  before update on public.agents
  for each row execute function public.set_updated_at();

-- Demo agent used by the operator console (no owner: service role only).
insert into public.agents (id, name, agency, instructions, tool_grants)
values (
  'agent-comms-01',
  'Agency Comms Agent',
  'Example Civic Agency',
  'You handle public communications for the agency. Research with data.gov and '
  'public web pages, then draft and send clear, accurate emails to residents.',
  array['email.send', 'web.scrape', 'datagov.search']
)
on conflict (id) do nothing;

notify pgrst, 'reload schema';
