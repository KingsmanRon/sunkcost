-- Access control (docs/ARCHITECTURE.md §8.1).
--
-- * authenticated (console, via PostgREST): SELECT only, on rows of orgs the user belongs to;
--   secret hashes are excluded by column grants.
-- * sunkcost_api / sunkcost_worker: login roles without BYPASSRLS. Each transaction runs
--   SET LOCAL app.org_id = '<uuid>', and every policy checks org_id against it.
-- * Cross-org reads go only through narrow SECURITY DEFINER functions with a pinned search_path.
-- * anon has no access. The service_role key is not used by any Sunkcost component.
--
-- Role passwords are not set here: the owner sets them per environment (supabase/seed.sql sets
-- local-only development passwords).

do $$
begin
  if not exists (select 1 from pg_roles where rolname = 'sunkcost_api') then
    create role sunkcost_api login noinherit nobypassrls;
  end if;
  if not exists (select 1 from pg_roles where rolname = 'sunkcost_worker') then
    create role sunkcost_worker login noinherit nobypassrls;
  end if;
end;
$$;

grant usage on schema public to sunkcost_api, sunkcost_worker;

-- Start from nothing: Supabase's default privileges grant anon and authenticated everything.
revoke all on all tables in schema public from public, anon, authenticated;
revoke all on all functions in schema public from public, anon, authenticated;
alter default privileges in schema public revoke all on tables from public, anon, authenticated;
alter default privileges in schema public revoke all on functions from public, anon, authenticated;

-- The org the current service transaction acts for; null when unset.
create or replace function public.current_org_id() returns uuid
language sql stable
set search_path = ''
as $$ select nullif(current_setting('app.org_id', true), '')::uuid $$;

-- Orgs the signed-in console user belongs to. SECURITY DEFINER so the org_members policy does not
-- recurse into itself; it only ever returns the caller's own memberships.
create or replace function public.my_org_ids() returns setof uuid
language sql stable security definer
set search_path = ''
as $$ select m.org_id from public.org_members m where m.user_id = auth.uid() $$;

grant execute on function public.current_org_id() to sunkcost_api, sunkcost_worker, authenticated;
grant execute on function public.my_org_ids() to authenticated;
grant execute on function public.uuid_generate_v7() to sunkcost_api, sunkcost_worker;

-- Row-level security on every org table: members read; the services act for app.org_id.
do $$
declare
  t text;
begin
  foreach t in array array[
    'orgs', 'org_members', 'projects', 'api_keys', 'workloads', 'runs', 'step_events',
    'policy_bundles', 'reports', 'jobs', 'audit_log'
  ] loop
    execute format('alter table public.%I enable row level security', t);
    execute format(
      'create policy member_read on public.%I for select to authenticated '
      'using (org_id in (select public.my_org_ids()))', t);
    execute format(
      'create policy service_rw on public.%I for all to sunkcost_api, sunkcost_worker '
      'using (org_id = public.current_org_id()) with check (org_id = public.current_org_id())', t);
  end loop;
end;
$$;

-- Console reads (SELECT only; never the secret hashes).
grant select (id, org_id, name, webhook_url, created_at) on public.orgs to authenticated;
grant select (id, org_id, project_id, name, prefix, scopes, created_by, created_at, revoked_at, last_used_at)
  on public.api_keys to authenticated;
grant select on public.org_members, public.projects, public.workloads, public.runs,
  public.step_events, public.policy_bundles, public.reports, public.jobs, public.audit_log
  to authenticated;

-- API: ingestion, outcomes, bundle serving, management endpoints, job enqueueing.
grant select, insert, update on public.orgs, public.org_members, public.projects,
  public.workloads, public.runs, public.step_events to sunkcost_api;
grant select, insert on public.api_keys to sunkcost_api;
grant update (revoked_at, last_used_at) on public.api_keys to sunkcost_api;
grant select, update on public.policy_bundles to sunkcost_api;
grant select, insert on public.jobs, public.audit_log to sunkcost_api;
grant select on public.reports, public.signing_keys to sunkcost_api;

-- Worker: calibration, bundle signing, reports, drift, retention, webhooks (P4).
grant select on public.orgs, public.projects, public.runs, public.step_events,
  public.signing_keys to sunkcost_worker;
grant select, update on public.workloads, public.jobs to sunkcost_worker;
grant select, insert, update on public.policy_bundles, public.reports to sunkcost_worker;
grant insert on public.jobs to sunkcost_worker;
grant select, insert on public.audit_log to sunkcost_worker;
grant delete on public.runs, public.step_events to sunkcost_worker;

-- audit_log is append-only for every role (the trigger also refuses the table owner).
revoke update, delete, truncate on public.audit_log from public, anon, authenticated,
  sunkcost_api, sunkcost_worker;

-- API-key lookup by prefix, before the org is known.
create or replace function public.resolve_api_key(p_prefix text)
returns table (id uuid, org_id uuid, project_id uuid, secret_sha256 text, scopes text[], revoked_at timestamptz)
language sql stable security definer
set search_path = ''
as $$
  select k.id, k.org_id, k.project_id, k.secret_sha256, k.scopes, k.revoked_at
  from public.api_keys k
  where k.prefix = p_prefix
$$;
revoke all on function public.resolve_api_key(text) from public, anon, authenticated;
grant execute on function public.resolve_api_key(text) to sunkcost_api;

-- The scheduler's cross-org scan (P4): which workloads exist, with what needs attention.
create or replace function public.list_due_work()
returns table (
  org_id uuid,
  workload_id uuid,
  active_bundle_id uuid,
  active_bundle_expires_at timestamptz,
  labelled_runs_since_active bigint
)
language sql stable security definer
set search_path = ''
as $$
  select w.org_id, w.id, b.id,
         (b.payload ->> 'expires_at')::timestamptz,
         (select count(*) from public.runs r
           where r.workload_id = w.id and r.outcome in ('success', 'failure')
             and (b.created_at is null or r.outcome_at > b.created_at))
  from public.workloads w
  left join public.policy_bundles b on b.workload_id = w.id and b.status = 'active'
$$;
revoke all on function public.list_due_work() from public, anon, authenticated;
grant execute on function public.list_due_work() to sunkcost_worker;

-- Partition maintenance runs as the table owner; the worker may call it.
alter function public.ensure_step_partitions(date, integer) security definer;
revoke all on function public.ensure_step_partitions(date, integer) from public, anon, authenticated;
grant execute on function public.ensure_step_partitions(date, integer) to sunkcost_worker;

-- New functions get EXECUTE for PUBLIC from the global default privileges, which the per-schema
-- defaults above cannot remove. Revoke it explicitly, then grant back only what each role needs.
revoke execute on all functions in schema public from public, anon, authenticated;
grant execute on function public.current_org_id() to sunkcost_api, sunkcost_worker, authenticated;
grant execute on function public.my_org_ids() to authenticated;
grant execute on function public.uuid_generate_v7() to sunkcost_api, sunkcost_worker;
grant execute on function public.resolve_api_key(text) to sunkcost_api;
grant execute on function public.list_due_work() to sunkcost_worker;
grant execute on function public.ensure_step_partitions(date, integer) to sunkcost_worker;
