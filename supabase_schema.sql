-- Create the analysis_cache table (v2)
create table public.analysis_cache (
  id uuid default gen_random_uuid() primary key,
  response_id uuid,
  repo_url text not null,
  commit_sha text not null,
  package_path text not null default '.',
  schema_version int not null default 2,
  build_status text not null default 'not_run'
    check (build_status in ('passed', 'failed', 'skipped', 'partial', 'error', 'not_run')),
  deploy_shape text,
  railpack_version text,
  pipeline_duration_ms int,
  workflow_version text,
  result jsonb not null,
  created_at timestamp with time zone default timezone('utc'::text, now()) not null,
  unique(repo_url, commit_sha, package_path)
);

create index if not exists idx_analysis_cache_schema_version
  on public.analysis_cache (schema_version);

create index if not exists idx_analysis_cache_build_status
  on public.analysis_cache (build_status, created_at desc);

create index if not exists idx_analysis_cache_repo_status
  on public.analysis_cache (repo_url, build_status);

alter table public.analysis_cache enable row level security;

create policy "Allow service role full access to analysis_cache"
  on public.analysis_cache
  as permissive
  for all
  to service_role
  using (true)
  with check (true);

-- Store every API response payload for audit/debugging (v2)
create table if not exists public.analysis_responses (
  id uuid default gen_random_uuid() primary key,
  endpoint text not null,
  repo_url text not null,
  commit_sha text,
  package_path text not null default '.',
  schema_version int not null default 2,
  build_status text not null default 'not_run',
  deploy_shape text,
  railpack_version text,
  from_cache boolean not null default false,
  passed boolean not null default false,
  payload jsonb not null,
  created_at timestamp with time zone default timezone('utc'::text, now()) not null
);

create index if not exists idx_analysis_responses_repo_created
  on public.analysis_responses (repo_url, created_at desc);

create index if not exists idx_analysis_responses_endpoint_created
  on public.analysis_responses (endpoint, created_at desc);

create index if not exists idx_analysis_responses_build_status
  on public.analysis_responses (build_status, created_at desc);

create index if not exists idx_analysis_responses_schema_version
  on public.analysis_responses (schema_version);

alter table public.analysis_responses enable row level security;

create policy "Allow service role full access to analysis_responses"
  on public.analysis_responses
  as permissive
  for all
  to service_role
  using (true)
  with check (true);
