-- SD-Artifacts v2 schema migration
-- Cache key: (repo_url, commit_sha, package_path) — service_name removed
-- Only schema_version=2 cache rows are valid after this migration
--
-- Safe to re-run after a partial failure.

-- analysis_cache: indexed columns (add without inline CHECK — backfill first)
alter table public.analysis_cache
  add column if not exists schema_version int not null default 2;

alter table public.analysis_cache
  add column if not exists build_status text;

update public.analysis_cache
  set build_status = 'not_run'
  where build_status is null or build_status = 'unknown';

alter table public.analysis_cache
  alter column build_status set default 'not_run';

alter table public.analysis_cache
  alter column build_status set not null;

alter table public.analysis_cache
  drop constraint if exists analysis_cache_build_status_check;

alter table public.analysis_cache
  add constraint analysis_cache_build_status_check
  check (build_status in ('passed', 'failed', 'skipped', 'partial', 'error', 'not_run'));

alter table public.analysis_cache
  add column if not exists deploy_shape text,
  add column if not exists railpack_version text,
  add column if not exists pipeline_duration_ms int,
  add column if not exists workflow_version text;

create index if not exists idx_analysis_cache_schema_version
  on public.analysis_cache (schema_version);

create index if not exists idx_analysis_cache_build_status
  on public.analysis_cache (build_status, created_at desc);

create index if not exists idx_analysis_cache_repo_status
  on public.analysis_cache (repo_url, build_status);

-- Drop old cache key (included service_name)
alter table public.analysis_cache
  drop constraint if exists analysis_cache_repo_url_commit_sha_package_path_service_name_key;

-- Collapse legacy rows that shared repo_url+commit_sha+package_path across service_name.
-- Keep the newest row per key.
delete from public.analysis_cache
where id in (
  select id
  from (
    select
      id,
      row_number() over (
        partition by repo_url, commit_sha, package_path
        order by created_at desc nulls last, id desc
      ) as rn
    from public.analysis_cache
  ) ranked
  where rn > 1
);

alter table public.analysis_cache drop column if exists service_name;

alter table public.analysis_cache
  drop constraint if exists analysis_cache_repo_url_commit_sha_package_path_key;

alter table public.analysis_cache
  add constraint analysis_cache_repo_url_commit_sha_package_path_key
  unique (repo_url, commit_sha, package_path);

-- analysis_responses: mirror indexed columns
alter table public.analysis_responses
  add column if not exists schema_version int not null default 2;

alter table public.analysis_responses
  add column if not exists build_status text;

update public.analysis_responses
  set build_status = 'not_run'
  where build_status is null or build_status = 'unknown';

alter table public.analysis_responses
  alter column build_status set default 'not_run';

alter table public.analysis_responses
  alter column build_status set not null;

alter table public.analysis_responses
  add column if not exists deploy_shape text,
  add column if not exists railpack_version text;

alter table public.analysis_responses drop column if exists service_name;

create index if not exists idx_analysis_responses_build_status
  on public.analysis_responses (build_status, created_at desc);

create index if not exists idx_analysis_responses_schema_version
  on public.analysis_responses (schema_version);
