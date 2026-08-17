-- Shared TECS project workspace. Project records are deliberately inaccessible
-- through the public Data API. The tecs-team-projects Edge Function is the only
-- application entry point and uses a server-side Supabase secret.
create table if not exists public.tecs_team_projects (
  id uuid primary key default gen_random_uuid(),
  project_name text not null,
  client text not null default '',
  consultant text not null default '',
  contractor text not null default '',
  reference text not null default '',
  status text not null default 'pending' check (status in ('pending', 'complete')),
  progress smallint not null default 0 check (progress between 0 and 100),
  missing_fields text[] not null default '{}',
  item_count integer not null default 0 check (item_count >= 0),
  draft jsonb not null,
  revision bigint not null default 1 check (revision > 0),
  created_at timestamptz not null default now(),
  updated_at timestamptz not null default now(),
  completed_at timestamptz
);

create index if not exists tecs_team_projects_status_updated_idx
  on public.tecs_team_projects (status, updated_at desc);

create index if not exists tecs_team_projects_updated_idx
  on public.tecs_team_projects (updated_at desc);

alter table public.tecs_team_projects enable row level security;
revoke all on table public.tecs_team_projects from anon, authenticated;

drop policy if exists "deny direct project access" on public.tecs_team_projects;
create policy "deny direct project access"
  on public.tecs_team_projects
  for all
  to anon, authenticated
  using (false)
  with check (false);

comment on table public.tecs_team_projects is
  'Team project drafts. Direct client access is denied; use tecs-team-projects Edge Function.';
