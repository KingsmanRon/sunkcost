-- Worker support (docs/ARCHITECTURE.md §4 C7, ADR-0006). Expand-only: nothing existing changes shape.

-- Jobs: an optional dedupe key makes scheduled enqueues idempotent, and the result is kept.
alter table public.jobs add column dedupe_key text;
alter table public.jobs add column result jsonb;
alter table public.jobs add column finished_at timestamptz;
create unique index jobs_dedupe_key on public.jobs (dedupe_key) where dedupe_key is not null;
alter table public.jobs drop constraint jobs_type_check;
alter table public.jobs add constraint jobs_type_check check (type in (
  'calibrate', 'resign_bundle', 'build_report', 'drift_check', 'retention', 'webhook'));

-- The scheduler's cross-org scan, now covering due jobs as well as workloads. A running job whose
-- lock is older than the 15-minute job timeout counts as due again (its worker died).
drop function public.list_due_work();
create function public.list_due_work()
returns table (kind text, org_id uuid, id uuid, detail jsonb)
language sql stable security definer
set search_path = ''
as $$
  select 'job', j.org_id, j.id, jsonb_build_object('type', j.type, 'attempts', j.attempts)
  from public.jobs j
  where (j.status in ('queued', 'failed') and j.run_after <= now())
     or (j.status = 'running' and j.locked_at < now() - interval '15 minutes')
  union all
  select 'workload', w.org_id, w.id, jsonb_build_object(
    'mode', w.mode,
    'override', w.override,
    'active_bundle_id', b.id,
    'active_issued_at', b.payload ->> 'issued_at',
    'labelled_runs_since_active', (
      select count(*) from public.runs r
      where r.workload_id = w.id and r.outcome in ('success', 'failure')
        and (b.activated_at is null or r.outcome_at > b.activated_at)))
  from public.workloads w
  left join public.policy_bundles b on b.workload_id = w.id and b.status = 'active'
$$;
revoke all on function public.list_due_work() from public, anon, authenticated;
grant execute on function public.list_due_work() to sunkcost_worker;

-- The worker publishes its signing key's public half (served by GET /v1/signing-keys).
grant insert, update on public.signing_keys to sunkcost_worker;

-- Webhook secrets. HMAC signing (§5.1) needs the secret itself, not only its hash, so the secret
-- is kept encrypted in Supabase Vault; orgs keeps the hash for display (ADR-0006).
alter table public.orgs add column webhook_secret_id uuid;

-- API, acting for p_org: set the webhook URL and secret (the secret is shown to the user once).
create function public.set_org_webhook(p_org uuid, p_url text, p_secret text) returns void
language plpgsql security definer
set search_path = ''
as $$
declare
  secret_id uuid;
begin
  if p_org is distinct from public.current_org_id() then
    raise exception 'not acting for this organisation' using errcode = '42501';
  end if;
  select o.webhook_secret_id into secret_id from public.orgs o where o.id = p_org;
  if secret_id is null then
    secret_id := vault.create_secret(p_secret, 'sunkcost-webhook-' || p_org::text);
  else
    perform vault.update_secret(secret_id, p_secret);
  end if;
  update public.orgs set webhook_url = p_url, webhook_secret_id = secret_id,
    webhook_secret_hash = encode(extensions.digest(p_secret, 'sha256'), 'hex')
  where id = p_org;
end;
$$;
revoke all on function public.set_org_webhook(uuid, text, text) from public, anon, authenticated;
grant execute on function public.set_org_webhook(uuid, text, text) to sunkcost_api;

-- Worker, acting for p_org: where to deliver, and the secret to sign with.
create function public.org_webhook_target(p_org uuid) returns table (url text, secret text)
language sql stable security definer
set search_path = ''
as $$
  select o.webhook_url, s.decrypted_secret
  from public.orgs o
  join vault.decrypted_secrets s on s.id = o.webhook_secret_id
  where o.id = p_org and p_org = public.current_org_id() and o.webhook_url is not null
$$;
revoke all on function public.org_webhook_target(uuid) from public, anon, authenticated;
grant execute on function public.org_webhook_target(uuid) to sunkcost_worker;

-- The column grants for the console were written before webhook_secret_id existed; it stays hidden.
