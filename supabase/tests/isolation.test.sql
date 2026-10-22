-- G5 isolation (docs/ARCHITECTURE.md §8.1, §13). Run with `make test-db` (supabase db reset +
-- supabase test db). Console access is exercised exactly as PostgREST does it: SET ROLE
-- authenticated with request.jwt.claims carrying the user id.
begin;
create extension if not exists pgtap with schema extensions;
select plan(65);

-- Fixtures, as the migration owner -------------------------------------------------------------
-- The test session switches into the service roles; membership lasts only for this transaction.
grant sunkcost_api, sunkcost_worker to current_user;
-- pgTAP lives in the extensions schema; let the service roles call it while they are tested.
grant usage on schema extensions to sunkcost_api, sunkcost_worker;
insert into auth.users (id, aud, role, email) values
  ('00000000-0000-4000-8000-00000000000a', 'authenticated', 'authenticated', 'a@example.com'),
  ('00000000-0000-4000-8000-00000000000b', 'authenticated', 'authenticated', 'b@example.com');

insert into public.orgs (id, name, webhook_secret_hash) values
  ('0aaaaaaa-0000-7000-8000-000000000001', 'org A', 'hash-a'),
  ('0bbbbbbb-0000-7000-8000-000000000001', 'org B', 'hash-b');
insert into public.org_members values
  ('0aaaaaaa-0000-7000-8000-000000000001', '00000000-0000-4000-8000-00000000000a', 'owner', now()),
  ('0bbbbbbb-0000-7000-8000-000000000001', '00000000-0000-4000-8000-00000000000b', 'owner', now());
insert into public.projects (id, org_id, name) values
  ('0aaaaaaa-0000-7000-8000-000000000002', '0aaaaaaa-0000-7000-8000-000000000001', 'p'),
  ('0bbbbbbb-0000-7000-8000-000000000002', '0bbbbbbb-0000-7000-8000-000000000001', 'p');
insert into public.api_keys (org_id, project_id, prefix, secret_sha256, scopes) values
  ('0aaaaaaa-0000-7000-8000-000000000001', '0aaaaaaa-0000-7000-8000-000000000002', 'aaaaaaaa', repeat('a', 64), '{ingest}'),
  ('0bbbbbbb-0000-7000-8000-000000000001', '0bbbbbbb-0000-7000-8000-000000000002', 'bbbbbbbb', repeat('b', 64), '{ingest}');
insert into public.workloads (id, org_id, project_id, name) values
  ('0aaaaaaa-0000-7000-8000-000000000003', '0aaaaaaa-0000-7000-8000-000000000001', '0aaaaaaa-0000-7000-8000-000000000002', 'w'),
  ('0bbbbbbb-0000-7000-8000-000000000003', '0bbbbbbb-0000-7000-8000-000000000001', '0bbbbbbb-0000-7000-8000-000000000002', 'w');
-- The same run_id in both orgs: runs are keyed per org, so they can coexist.
insert into public.runs (org_id, id, project_id, workload_id, started_at) values
  ('0aaaaaaa-0000-7000-8000-000000000001', '01a15eb1-0000-7000-8000-000000000009', '0aaaaaaa-0000-7000-8000-000000000002', '0aaaaaaa-0000-7000-8000-000000000003', now()),
  ('0bbbbbbb-0000-7000-8000-000000000001', '01a15eb1-0000-7000-8000-000000000009', '0bbbbbbb-0000-7000-8000-000000000002', '0bbbbbbb-0000-7000-8000-000000000003', now());
insert into public.step_events (org_id, run_id, seq, run_month, ts, kind, input_tokens, output_tokens, error)
  select org_id, id, 1, run_month, now(), 'tool', 0, 0, false from public.runs;
insert into public.policy_bundles (org_id, workload_id, version, status, payload, signature, key_id)
  select org_id, id, 1, 'active', '{}', 'sig', 'k' from public.workloads;
insert into public.reports (org_id, workload_id, period_start, period_end)
  select org_id, id, now() - interval '1 day', now() from public.workloads;
insert into public.jobs (org_id, type) select id, 'calibrate' from public.orgs;
insert into public.audit_log (org_id, actor, action) select id, 'system', 'fixture' from public.orgs;

create temporary table org_tables (name text) on commit drop;
insert into org_tables values ('orgs'), ('org_members'), ('projects'), ('api_keys'), ('workloads'),
  ('runs'), ('step_events'), ('policy_bundles'), ('reports'), ('jobs'), ('audit_log');
grant select on org_tables to authenticated, anon, sunkcost_api, sunkcost_worker;

-- Counts every org table as the current role; used to compare tenants in one assertion.
create function pg_temp.visible(p_org uuid) returns bigint language plpgsql as $$
declare t text; n bigint; total bigint := 0;
begin
  for t in select name from org_tables loop
    continue when not has_any_column_privilege('public.' || t, 'SELECT');
    execute format('select count(*) from public.%I where org_id = $1', t) into n using p_org;
    total := total + n;
  end loop;
  return total;
end $$;
grant execute on function pg_temp.visible(uuid) to authenticated, anon, sunkcost_api, sunkcost_worker;

-- Catalogue ---------------------------------------------------------------------------------------
select is((select count(*)::int from pg_class c join org_tables t on c.relname = t.name
           where c.relnamespace = 'public'::regnamespace and c.relrowsecurity), 11,
          'RLS is enabled on every org table');
select is((select count(*)::int from information_schema.columns c join org_tables t on c.table_name = t.name
           where c.table_schema = 'public' and c.column_name = 'org_id'), 11, 'every org table has org_id');
select ok(not exists (select 1 from pg_roles where rolname in ('sunkcost_api', 'sunkcost_worker') and rolbypassrls),
          'service roles do not bypass RLS');
select ok(not exists (select 1 from information_schema.role_table_grants
                      where grantee = 'anon' and table_schema = 'public'), 'anon has no table grants');
select ok(not exists (select 1 from information_schema.role_table_grants
                      where grantee = 'authenticated' and table_schema = 'public' and privilege_type <> 'SELECT'),
          'authenticated holds SELECT only');
select ok(not exists (select 1 from information_schema.column_privileges
                      where grantee = 'authenticated' and table_schema = 'public'
                        and column_name in ('secret_sha256', 'webhook_secret_hash')),
          'secret hashes are not granted to the console');
select is((select string_agg(p.proname, ',' order by p.proname) from pg_proc p
           where p.pronamespace = 'public'::regnamespace and p.prosecdef),
          'ensure_step_partitions,list_due_work,my_org_ids,org_webhook_target,resolve_api_key,set_org_webhook',
          'exactly the expected SECURITY DEFINER functions');
select ok(not exists (select 1 from pg_proc p where p.pronamespace = 'public'::regnamespace and p.prosecdef
                      and not exists (select 1 from unnest(p.proconfig) c where c like 'search_path=%')),
          'every definer function pins search_path');

-- Console user A, as PostgREST runs it -------------------------------------------------------------
set local role authenticated;
set local request.jwt.claims = '{"sub": "00000000-0000-4000-8000-00000000000a", "role": "authenticated"}';
select ok(pg_temp.visible('0aaaaaaa-0000-7000-8000-000000000001') >= 11, 'A sees its own rows in every table');
select is(pg_temp.visible('0bbbbbbb-0000-7000-8000-000000000001'), 0::bigint, 'A sees nothing of B');
select is((select count(*)::int from public.runs where id = '01a15eb1-0000-7000-8000-000000000009'), 1,
          'A sees only its own copy of a shared run_id');
select is((select array_agg(x) from public.my_org_ids() x), array['0aaaaaaa-0000-7000-8000-000000000001'::uuid],
          'my_org_ids returns only the caller''s orgs');
select throws_ok('select secret_sha256 from public.api_keys', '42501', null, 'A cannot read key hashes');
select throws_ok('select webhook_secret_hash from public.orgs', '42501', null, 'A cannot read webhook hashes');
select lives_ok('select id, prefix, scopes from public.api_keys', 'A can list key metadata');
select throws_ok($$insert into public.projects (org_id, name) values ('0aaaaaaa-0000-7000-8000-000000000001', 'x')$$,
                 '42501', null, 'console cannot insert');
select throws_ok($$update public.workloads set mode = 'enforce'$$, '42501', null, 'console cannot update');
select throws_ok($$delete from public.runs$$, '42501', null, 'console cannot delete');
select throws_ok($$insert into public.audit_log (org_id, actor, action) values ('0aaaaaaa-0000-7000-8000-000000000001', 'x', 'y')$$,
                 '42501', null, 'console cannot write the audit log');
select throws_ok('select * from public.resolve_api_key(''aaaaaaaa'')', '42501', null,
                 'console cannot call resolve_api_key');
select throws_ok('select * from public.list_due_work()', '42501', null, 'console cannot call list_due_work');
select throws_ok($$select public.set_org_webhook('0aaaaaaa-0000-7000-8000-000000000001', 'https://x.test', 's')$$,
                 '42501', null, 'console cannot set webhooks');
select throws_ok($$select * from public.org_webhook_target('0aaaaaaa-0000-7000-8000-000000000001')$$,
                 '42501', null, 'console cannot read webhook secrets');
select throws_ok('select public.ensure_step_partitions(now()::date, 1)', '42501', null,
                 'console cannot create partitions');
select throws_ok(format('select count(*) from public.%I', 'step_events_default'), '42501', null,
                 'console cannot read partitions directly');
select throws_ok('select count(*) from public.signing_keys', '42501', null, 'console has no signing_keys grant');

-- Console user B sees the mirror image.
set local request.jwt.claims = '{"sub": "00000000-0000-4000-8000-00000000000b", "role": "authenticated"}';
select is(pg_temp.visible('0aaaaaaa-0000-7000-8000-000000000001'), 0::bigint, 'B sees nothing of A');
select ok(pg_temp.visible('0bbbbbbb-0000-7000-8000-000000000001') >= 11, 'B sees its own rows');

-- A user with no membership sees nothing.
set local request.jwt.claims = '{"sub": "00000000-0000-4000-8000-0000000000ff", "role": "authenticated"}';
select is(pg_temp.visible('0aaaaaaa-0000-7000-8000-000000000001') + pg_temp.visible('0bbbbbbb-0000-7000-8000-000000000001'),
          0::bigint, 'a non-member sees nothing');
reset role;

-- anon --------------------------------------------------------------------------------------------
set local role anon;
select throws_ok('select count(*) from public.runs', '42501', null, 'anon cannot read runs');
select throws_ok('select count(*) from public.orgs', '42501', null, 'anon cannot read orgs');
select throws_ok('select * from public.my_org_ids()', '42501', null, 'anon cannot call my_org_ids');
reset role;

-- API role ----------------------------------------------------------------------------------------
set local role sunkcost_api;
select is(pg_temp.visible('0aaaaaaa-0000-7000-8000-000000000001'), 0::bigint, 'API sees nothing without app.org_id');
set local app.org_id = '0aaaaaaa-0000-7000-8000-000000000001';
select ok(pg_temp.visible('0aaaaaaa-0000-7000-8000-000000000001') >= 11, 'API sees org A when acting for A');
select is(pg_temp.visible('0bbbbbbb-0000-7000-8000-000000000001'), 0::bigint, 'API acting for A sees nothing of B');
select throws_ok($$insert into public.projects (org_id, name) values ('0bbbbbbb-0000-7000-8000-000000000001', 'evil')$$,
                 '42501', null, 'API acting for A cannot write rows of B');
select lives_ok($$insert into public.projects (org_id, name) values ('0aaaaaaa-0000-7000-8000-000000000001', 'ok')$$,
                'API acting for A can write rows of A');
update public.workloads set mode = 'enforce' where org_id = '0bbbbbbb-0000-7000-8000-000000000001';
select is((select count(*)::int from public.workloads where mode = 'enforce'), 0, 'API acting for A updates nothing of B');
select throws_ok($$update public.orgs set org_id = org_id$$, '428C9', null, 'generated org_id cannot be rewritten');
select is((select count(*)::int from public.resolve_api_key('bbbbbbbb')), 1,
          'resolve_api_key finds a key before the org is known');
select is((select org_id from public.resolve_api_key('bbbbbbbb')), '0bbbbbbb-0000-7000-8000-000000000001'::uuid,
          'and reports which org it belongs to');
select throws_ok('select * from public.list_due_work()', '42501', null, 'API cannot call list_due_work');
select throws_ok($$select public.set_org_webhook('0bbbbbbb-0000-7000-8000-000000000001', 'https://x.test', 's')$$,
                 '42501', null, 'API acting for A cannot set the webhook of B');
select lives_ok($$select public.set_org_webhook('0aaaaaaa-0000-7000-8000-000000000001', 'https://a.test/hook', 'sekrit')$$,
                'API acting for A can set the webhook of A');
select is((select webhook_secret_hash from public.orgs where id = '0aaaaaaa-0000-7000-8000-000000000001'),
          encode(extensions.digest('sekrit', 'sha256'), 'hex'), 'only the secret''s hash is stored on orgs');
select throws_ok($$select * from public.org_webhook_target('0aaaaaaa-0000-7000-8000-000000000001')$$,
                 '42501', null, 'API cannot read webhook secrets');
select throws_ok($$update public.api_keys set secret_sha256 = repeat('c', 64)$$, '42501', null,
                 'API cannot rewrite key hashes');
select throws_ok('delete from public.runs', '42501', null, 'API cannot delete runs');
select throws_ok($$update public.audit_log set action = 'x'$$, '42501', null, 'API cannot update the audit log');
select throws_ok('delete from public.audit_log', '42501', null, 'API cannot delete from the audit log');
reset role;

-- Worker role -------------------------------------------------------------------------------------
set local role sunkcost_worker;
set local app.org_id = '0bbbbbbb-0000-7000-8000-000000000001';
select is(pg_temp.visible('0aaaaaaa-0000-7000-8000-000000000001'), 0::bigint, 'worker acting for B sees nothing of A');
select ok(pg_temp.visible('0bbbbbbb-0000-7000-8000-000000000001') >= 9, 'worker acting for B sees B');
select is((select count(*)::int from public.list_due_work() where kind = 'workload'), 2,
          'list_due_work spans orgs (scheduler only)');
select throws_ok('select * from public.resolve_api_key(''aaaaaaaa'')', '42501', null,
                 'worker cannot call resolve_api_key');
select throws_ok($$insert into public.policy_bundles (org_id, workload_id, version, status, payload, signature, key_id)
                   values ('0aaaaaaa-0000-7000-8000-000000000001', '0aaaaaaa-0000-7000-8000-000000000003', 2, 'validated', '{}', 's', 'k')$$,
                 '42501', null, 'worker acting for B cannot write bundles for A');
select throws_ok($$update public.audit_log set action = 'x'$$, '42501', null, 'worker cannot update the audit log');
select is((select count(*)::int from public.org_webhook_target('0aaaaaaa-0000-7000-8000-000000000001')), 0,
          'worker acting for B cannot read the webhook secret of A');
set local app.org_id = '0aaaaaaa-0000-7000-8000-000000000001';
select is((select secret from public.org_webhook_target('0aaaaaaa-0000-7000-8000-000000000001')), 'sekrit',
          'worker acting for A reads the webhook secret of A, to sign deliveries');
set local app.org_id = '0bbbbbbb-0000-7000-8000-000000000001';
select lives_ok('select public.ensure_step_partitions(now()::date, 2)', 'worker can maintain partitions');
reset role;

-- audit_log is append-only even for the table owner -------------------------------------------------
select throws_ok($$update public.audit_log set action = 'x'$$, '42501', null, 'owner cannot update the audit log');
select throws_ok('delete from public.audit_log', '42501', null, 'owner cannot delete from the audit log');
select throws_ok('truncate public.audit_log', '42501', null, 'owner cannot truncate the audit log');

-- Integrity -----------------------------------------------------------------------------------------
select throws_ok($$insert into public.policy_bundles (org_id, workload_id, version, status, payload, signature, key_id)
                   values ('0aaaaaaa-0000-7000-8000-000000000001', '0aaaaaaa-0000-7000-8000-000000000003', 3, 'active', '{}', 's', 'k')$$,
                 '23505', null, 'at most one active bundle per workload');
select is((select status from public.runs where org_id = '0aaaaaaa-0000-7000-8000-000000000001'), 'running',
          'run status is derived (running)');
select throws_ok($$insert into public.step_events (org_id, run_id, seq, run_month, ts, kind, input_tokens, output_tokens, error)
                   select org_id, id, 1, run_month, now(), 'tool', 0, 0, false from public.runs
                   where org_id = '0aaaaaaa-0000-7000-8000-000000000001'$$,
                 '23505', null, 'a step is stored once per (run, seq)');

select * from finish();
rollback;
