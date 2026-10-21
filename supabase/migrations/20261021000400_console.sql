-- P5 (ADR-0007): what the console needs to decide whether a workload may be switched to enforce.
-- Expand only: new nullable/defaulted columns, so the deployed worker keeps working unchanged.

-- Runs in the period that were enforced, and the earliest run start in it. A "shadow report
-- covering 7 days" is a report of ≥ 7 days with runs, none enforced, whose runs start on its
-- first day (§4 C9).
alter table public.reports
  add column enforced_runs integer not null default 0 check (enforced_runs >= 0),
  add column first_run_at timestamptz;
