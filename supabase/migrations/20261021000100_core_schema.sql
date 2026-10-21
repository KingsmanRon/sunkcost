-- Sunkcost v1 schema (docs/ARCHITECTURE.md §8, ADR-0005).
-- Every table carries org_id. Money is numeric(20,9). Timestamps are timestamptz.

-- UUIDv7 (RFC 9562 §5.7): 48-bit Unix milliseconds, version 7, variant 10, 74 random bits.
create or replace function public.uuid_generate_v7() returns uuid
language sql volatile parallel safe
set search_path = ''
as $$
  select encode(
    set_bit(
      set_bit(
        overlay(uuid_send(gen_random_uuid())
                placing substring(int8send((extract(epoch from clock_timestamp()) * 1000)::bigint) from 3)
                from 1 for 6),
        52, 1),
      53, 1),
    'hex')::uuid;
$$;

create table public.orgs (
  id uuid primary key default public.uuid_generate_v7(),
  -- Every table has org_id (§8); for orgs it is the row's own id.
  org_id uuid generated always as (id) stored,
  name text not null check (length(name) between 1 and 256),
  webhook_url text check (webhook_url is null or webhook_url ~ '^https://'),
  webhook_secret_hash text,
  created_at timestamptz not null default now()
);

create table public.org_members (
  org_id uuid not null references public.orgs (id) on delete cascade,
  user_id uuid not null references auth.users (id) on delete cascade,
  role text not null check (role in ('owner', 'admin', 'member', 'viewer')),
  created_at timestamptz not null default now(),
  primary key (org_id, user_id)
);
create index org_members_user_idx on public.org_members (user_id);

create table public.projects (
  id uuid primary key default public.uuid_generate_v7(),
  org_id uuid not null references public.orgs (id) on delete cascade,
  name text not null check (length(name) between 1 and 256),
  created_at timestamptz not null default now(),
  unique (org_id, name),
  unique (org_id, id)
);

create table public.api_keys (
  id uuid primary key default public.uuid_generate_v7(),
  org_id uuid not null references public.orgs (id) on delete cascade,
  project_id uuid not null,
  name text not null default '' check (length(name) <= 256),
  -- Key format sc_live_<prefix8>_<secret32>; only the SHA-256 of the full key is stored.
  prefix text not null unique check (prefix ~ '^[a-z0-9]{8}$'),
  secret_sha256 text not null check (secret_sha256 ~ '^[0-9a-f]{64}$'),
  scopes text[] not null check (scopes <@ array['ingest', 'outcome:write', 'bundle:read']::text[]),
  created_by uuid references auth.users (id) on delete set null,
  created_at timestamptz not null default now(),
  revoked_at timestamptz,
  last_used_at timestamptz,
  foreign key (org_id, project_id) references public.projects (org_id, id) on delete cascade
);

create table public.workloads (
  id uuid primary key default public.uuid_generate_v7(),
  org_id uuid not null references public.orgs (id) on delete cascade,
  project_id uuid not null,
  name text not null check (name ~ '^[A-Za-z0-9][A-Za-z0-9._-]{0,127}$'),
  mode text not null default 'shadow' check (mode in ('shadow', 'enforce')),
  override text check (override is null or override = 'shadow'),
  target_recall numeric(5,4) not null default 0.95 check (target_recall > 0 and target_recall < 1),
  delta numeric(5,4) not null default 0.05 check (delta > 0 and delta < 1),
  min_steps integer not null default 3 check (min_steps between 0 and 1000000),
  holdout_rate numeric(5,4) not null default 0.05 check (holdout_rate between 0 and 1),
  min_savings_share numeric(5,4) not null default 0.02 check (min_savings_share between 0 and 1),
  created_at timestamptz not null default now(),
  updated_at timestamptz not null default now(),
  unique (project_id, name),
  unique (org_id, id),
  foreign key (org_id, project_id) references public.projects (org_id, id) on delete cascade
);

-- Runs are keyed by (org_id, id): a run_id chosen by one tenant can never collide with another's.
create table public.runs (
  org_id uuid not null references public.orgs (id) on delete cascade,
  id uuid not null,
  project_id uuid not null,
  workload_id uuid,  -- null only while the run is a placeholder (no run_start seen yet)
  external_ref text check (external_ref is null or length(external_ref) between 1 and 128),
  -- First time the server saw any event of this run; partitions step_events (ADR-0005).
  received_at timestamptz not null default now(),
  run_month date not null default date_trunc('month', now() at time zone 'utc')::date,
  started_at timestamptz,
  ended_at timestamptz,
  end_status text check (end_status in ('completed', 'aborted', 'errored')),
  status text not null generated always as (
    case
      when workload_id is null then 'partial'
      when end_status is null then 'running'
      else end_status
    end
  ) stored,
  steps integer check (steps between 0 and 1000000),
  total_cost_usd numeric(20,9) check (total_cost_usd >= 0),
  mode text check (mode in ('shadow', 'enforce')),
  holdout boolean,
  bundle_id uuid,
  bundle_version integer,
  would_abort_step integer check (would_abort_step between 0 and 1000000),
  cost_through_would_abort numeric(20,9) check (cost_through_would_abort >= 0),
  aborted_step integer check (aborted_step between 0 and 1000000),
  outcome text check (outcome in ('success', 'failure', 'abandoned')),
  outcome_source text,
  outcome_at timestamptz,
  outcome_conflict boolean not null default false,
  primary key (org_id, id),
  foreign key (org_id, project_id) references public.projects (org_id, id) on delete cascade,
  foreign key (org_id, workload_id) references public.workloads (org_id, id) on delete cascade,
  check (workload_id is not null or started_at is null)
);
create index runs_workload_outcome_idx on public.runs (workload_id, outcome_at);
create index runs_workload_ref_idx on public.runs (workload_id, external_ref);
create index runs_received_idx on public.runs (received_at);

-- Steps are range-partitioned by the month their run was first received (server clock), so a
-- re-sent step always lands in the same partition and (run_id, seq) stays unique (ADR-0005).
create table public.step_events (
  org_id uuid not null,
  run_id uuid not null,
  seq integer not null check (seq between 1 and 1000000),
  run_month date not null,
  ts timestamptz not null,
  received_at timestamptz not null default now(),
  kind text not null check (kind in ('model', 'tool')),
  model text,
  tool_name text,
  args_hash text check (args_hash is null or args_hash ~ '^hmac-sha256:[0-9a-f]{64}$'),
  input_tokens integer not null check (input_tokens >= 0),
  output_tokens integer not null check (output_tokens >= 0),
  cost_usd numeric(20,9) check (cost_usd >= 0),
  error boolean not null,
  latency_ms integer check (latency_ms >= 0),
  -- The check() decision made before this step (null when check() was not called).
  action text check (action in ('CONTINUE', 'ABORT')),
  score double precision,
  threshold double precision,
  would_abort boolean,
  reason text,
  primary key (org_id, run_id, seq, run_month),
  foreign key (org_id, run_id) references public.runs (org_id, id) on delete cascade
) partition by range (run_month);
create table public.step_events_default partition of public.step_events default;

-- Monthly partitions from `from_month` for `months` months; idempotent. The worker calls it daily.
create or replace function public.ensure_step_partitions(from_month date, months integer)
returns integer
language plpgsql
set search_path = ''
as $$
declare
  m date;
  created integer := 0;
  part text;
begin
  for i in 0 .. months - 1 loop
    m := (date_trunc('month', from_month) + make_interval(months => i))::date;
    part := format('step_events_%s', to_char(m, 'YYYY_MM'));
    if to_regclass('public.' || part) is null then
      execute format(
        'create table public.%I partition of public.step_events for values from (%L) to (%L)',
        part, m, (m + interval '1 month')::date);
      created := created + 1;
    end if;
  end loop;
  return created;
end;
$$;
select public.ensure_step_partitions((now() - interval '3 months')::date, 6);

create table public.policy_bundles (
  id uuid primary key default public.uuid_generate_v7(),
  org_id uuid not null references public.orgs (id) on delete cascade,
  workload_id uuid not null,
  version integer not null check (version >= 1),
  status text not null check (status in ('validated', 'rejected', 'active', 'retired')),
  payload jsonb not null,  -- the signed bundle.v1 document
  signature text,          -- base64 Ed25519 over canonical_json(payload); null when rejected
  key_id text,
  metrics jsonb not null default '{}'::jsonb,
  reason text,
  created_at timestamptz not null default now(),
  activated_at timestamptz,
  unique (workload_id, version),
  unique (org_id, id),
  foreign key (org_id, workload_id) references public.workloads (org_id, id) on delete cascade,
  check (status = 'rejected' or (signature is not null and key_id is not null))
);
create unique index policy_bundles_one_active on public.policy_bundles (workload_id)
  where status = 'active';

create table public.reports (
  id uuid primary key default public.uuid_generate_v7(),
  org_id uuid not null references public.orgs (id) on delete cascade,
  workload_id uuid not null,
  period_start timestamptz not null,
  period_end timestamptz not null,
  runs integer not null default 0,
  verified_saving_usd numeric(20,9) not null default 0,
  estimated_saving_usd numeric(20,9) not null default 0,
  failures_caught integer not null default 0,
  successes_lost integer not null default 0,
  false_abort_rate double precision,
  fa_ci_low double precision,
  fa_ci_high double precision,
  created_at timestamptz not null default now(),
  unique (workload_id, period_start, period_end),
  foreign key (org_id, workload_id) references public.workloads (org_id, id) on delete cascade
);

create table public.jobs (
  id uuid primary key default public.uuid_generate_v7(),
  org_id uuid not null references public.orgs (id) on delete cascade,
  type text not null check (type in ('calibrate', 'resign_bundle', 'build_report', 'drift_check', 'retention')),
  payload jsonb not null default '{}'::jsonb,
  status text not null default 'queued' check (status in ('queued', 'running', 'done', 'failed', 'dead')),
  attempts integer not null default 0,
  run_after timestamptz not null default now(),
  locked_at timestamptz,
  error text,
  created_at timestamptz not null default now(),
  updated_at timestamptz not null default now()
);
create index jobs_claim_idx on public.jobs (status, run_after) where status in ('queued', 'failed');

create table public.audit_log (
  id uuid primary key default public.uuid_generate_v7(),
  org_id uuid not null references public.orgs (id) on delete cascade,
  actor text not null,  -- a user id, or 'system'
  action text not null,
  target text,
  before jsonb,
  after jsonb,
  at timestamptz not null default now()
);
create index audit_log_org_at_idx on public.audit_log (org_id, at);

-- Append-only, enforced for every role including table owners (grants are revoked as well).
create or replace function public.audit_log_immutable() returns trigger
language plpgsql
set search_path = ''
as $$
begin
  raise exception 'audit_log is append-only' using errcode = '42501';
end;
$$;
create trigger audit_log_no_update before update or delete on public.audit_log
  for each row execute function public.audit_log_immutable();
create trigger audit_log_no_truncate before truncate on public.audit_log
  for each statement execute function public.audit_log_immutable();

-- Public halves of the bundle-signing keys, served at GET /v1/signing-keys (no org_id: global).
create table public.signing_keys (
  key_id text primary key check (key_id ~ '^[a-z0-9][a-z0-9-]{0,63}$'),
  public_key_b64 text not null,
  created_at timestamptz not null default now(),
  retired_at timestamptz
);
