import "server-only";

import { notFound } from "next/navigation";

import { createClient, currentUser } from "@/lib/supabase/server";

// Reads as the signed-in user through PostgREST; RLS returns only rows of the user's orgs (§8.1).
// numeric columns are selected ::text so money and settings stay decimal strings end to end.

export type Role = "owner" | "admin" | "member" | "viewer";

export interface Org {
  id: string;
  name: string;
  webhook_url: string | null;
  created_at: string;
  role: Role;
}

export interface Project {
  id: string;
  name: string;
  created_at: string;
}

export interface ApiKey {
  id: string;
  project_id: string;
  name: string;
  prefix: string;
  scopes: string[];
  created_at: string;
  revoked_at: string | null;
  last_used_at: string | null;
}

export interface Workload {
  id: string;
  project_id: string;
  name: string;
  mode: "shadow" | "enforce";
  override: "shadow" | null;
  target_recall: string;
  delta: string;
  min_steps: number;
  holdout_rate: string;
  created_at: string;
}

export interface Bundle {
  id: string;
  version: number;
  status: "validated" | "rejected" | "active" | "retired";
  reason: string | null;
  metrics: Record<string, unknown> | null;
  created_at: string;
  activated_at: string | null;
}

export interface Report {
  id: string;
  workload_id: string;
  period_start: string;
  period_end: string;
  runs: number;
  verified_saving_usd: string;
  estimated_saving_usd: string;
  failures_caught: number;
  successes_lost: number;
  false_abort_rate: number | null;
  fa_ci_low: number | null;
  fa_ci_high: number | null;
  enforced_runs: number;
  first_run_at: string | null;
}

export interface Run {
  id: string;
  workload_id: string;
  started_at: string | null;
  status: string;
  outcome: string | null;
  steps: number | null;
  total_cost_usd: string | null;
  mode: string | null;
  holdout: boolean | null;
  bundle_version: number | null;
  would_abort_step: number | null;
  aborted_step: number | null;
}

export interface Step {
  seq: number;
  ts: string;
  kind: "model" | "tool";
  model: string | null;
  tool_name: string | null;
  cost_usd: string | null;
  error: boolean;
  action: "CONTINUE" | "ABORT" | null;
  score: number | null;
  threshold: number | null;
  would_abort: boolean | null;
  reason: string | null;
}

export interface Job {
  id: string;
  type: string;
  status: string;
  error: string | null;
  created_at: string;
}

export interface AuditEntry {
  id: string;
  actor: string;
  action: string;
  target: string;
  before: unknown;
  after: unknown;
  at: string;
}

const WORKLOAD = "id, project_id, name, mode, override, target_recall::text, delta::text, min_steps, holdout_rate::text, created_at";
const REPORT = "id, workload_id, period_start, period_end, runs, verified_saving_usd::text, estimated_saving_usd::text, failures_caught, successes_lost, false_abort_rate, fa_ci_low, fa_ci_high, enforced_runs, first_run_at";
const RUN = "id, workload_id, started_at, status, outcome, steps, total_cost_usd::text, mode, holdout, bundle_version, would_abort_step, aborted_step";

function rows<T>(result: { data: unknown; error: { message: string } | null }): T[] {
  if (result.error) throw new Error(result.error.message);
  return (result.data ?? []) as T[];
}

export async function myOrgs(): Promise<Org[]> {
  const user = await currentUser();
  if (!user) return [];
  const supabase = await createClient();
  const [orgs, members] = await Promise.all([
    supabase.from("orgs").select("id, name, webhook_url, created_at").order("created_at"),
    supabase.from("org_members").select("org_id, role").eq("user_id", user.id),
  ]);
  const roles = new Map(
    rows<{ org_id: string; role: Role }>(members).map((m) => [m.org_id, m.role]),
  );
  return rows<Omit<Org, "role">>(orgs).flatMap((o) => {
    const role = roles.get(o.id);
    return role ? [{ ...o, role }] : [];
  });
}

/** The org, if the user belongs to it; otherwise a 404 (never a hint that it exists). */
export async function org(id: string): Promise<Org> {
  const found = (await myOrgs()).find((o) => o.id === id);
  if (!found) notFound();
  return found;
}

export function isAdmin(o: Org): boolean {
  return o.role === "owner" || o.role === "admin";
}

export async function projects(orgId: string): Promise<Project[]> {
  const supabase = await createClient();
  return rows(
    await supabase
      .from("projects")
      .select("id, name, created_at")
      .eq("org_id", orgId)
      .order("created_at"),
  );
}

export async function apiKeys(orgId: string): Promise<ApiKey[]> {
  const supabase = await createClient();
  return rows(
    await supabase
      .from("api_keys")
      .select("id, project_id, name, prefix, scopes, created_at, revoked_at, last_used_at")
      .eq("org_id", orgId)
      .order("created_at", { ascending: false }),
  );
}

export async function workloads(orgId: string): Promise<Workload[]> {
  const supabase = await createClient();
  return rows(
    await supabase.from("workloads").select(WORKLOAD).eq("org_id", orgId).order("name"),
  );
}

export async function workload(orgId: string, id: string): Promise<Workload> {
  const supabase = await createClient();
  const found = rows<Workload>(
    await supabase.from("workloads").select(WORKLOAD).eq("org_id", orgId).eq("id", id),
  );
  if (!found[0]) notFound();
  return found[0];
}

export async function bundles(orgId: string, workloadIds: string[]): Promise<(Bundle & { workload_id: string })[]> {
  if (workloadIds.length === 0) return [];
  const supabase = await createClient();
  return rows(
    await supabase
      .from("policy_bundles")
      .select("id, workload_id, version, status, reason, metrics, created_at, activated_at")
      .eq("org_id", orgId)
      .in("workload_id", workloadIds)
      .order("version", { ascending: false }),
  );
}

export async function reports(orgId: string, workloadIds: string[], days = 60): Promise<Report[]> {
  if (workloadIds.length === 0) return [];
  const supabase = await createClient();
  const since = new Date(Date.now() - days * 86_400_000).toISOString();
  return rows(
    await supabase
      .from("reports")
      .select(REPORT)
      .eq("org_id", orgId)
      .in("workload_id", workloadIds)
      .gte("period_end", since)
      .order("period_start"),
  );
}

export async function runs(orgId: string, workloadId: string, limit = 50): Promise<Run[]> {
  const supabase = await createClient();
  return rows(
    await supabase
      .from("runs")
      .select(RUN)
      .eq("org_id", orgId)
      .eq("workload_id", workloadId)
      .order("started_at", { ascending: false, nullsFirst: false })
      .limit(limit),
  );
}

export async function run(orgId: string, id: string): Promise<Run> {
  const supabase = await createClient();
  const found = rows<Run>(await supabase.from("runs").select(RUN).eq("org_id", orgId).eq("id", id));
  if (!found[0]) notFound();
  return found[0];
}

export async function steps(orgId: string, runId: string): Promise<Step[]> {
  const supabase = await createClient();
  return rows(
    await supabase
      .from("step_events")
      .select(
        "seq, ts, kind, model, tool_name, cost_usd::text, error, action, score, threshold, would_abort, reason",
      )
      .eq("org_id", orgId)
      .eq("run_id", runId)
      .order("seq")
      .limit(1000),
  );
}

export async function jobs(orgId: string, workloadId: string, limit = 10): Promise<Job[]> {
  const supabase = await createClient();
  return rows(
    await supabase
      .from("jobs")
      .select("id, type, status, error, created_at")
      .eq("org_id", orgId)
      .contains("payload", { workload_id: workloadId })
      .order("created_at", { ascending: false })
      .limit(limit),
  );
}

export async function audit(orgId: string, limit = 200): Promise<AuditEntry[]> {
  const supabase = await createClient();
  return rows(
    await supabase
      .from("audit_log")
      .select("id, actor, action, target, before, after, at")
      .eq("org_id", orgId)
      .order("at", { ascending: false })
      .limit(limit),
  );
}

/** Ended runs still waiting for an outcome: excluded from calibration and reports (§10). */
export async function outcomePending(orgId: string, workloadId: string): Promise<number> {
  const supabase = await createClient();
  const { count, error } = await supabase
    .from("runs")
    .select("id", { count: "exact", head: true })
    .eq("org_id", orgId)
    .eq("workload_id", workloadId)
    .is("outcome", null)
    .not("end_status", "is", null);
  if (error) throw new Error(error.message);
  return count ?? 0;
}
