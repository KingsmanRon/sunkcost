"use server";

import { refresh } from "next/cache";
import { headers } from "next/headers";
import { redirect } from "next/navigation";

import type { ActionState } from "@/lib/action-state";
import { api, attempt } from "@/lib/api";
import { createClient } from "@/lib/supabase/server";

// Every mutation goes through the API as the signed-in user; the API checks membership and role
// and writes the audit log (§4 C6, C9). Form fields are untrusted input, validated there.

function field(form: FormData, name: string): string {
  const value = form.get(name);
  return typeof value === "string" ? value.trim() : "";
}

export async function signIn(_: ActionState, form: FormData): Promise<ActionState> {
  const email = field(form, "email");
  if (!/^[^\s@]+@[^\s@]+$/.test(email)) return { ok: false, message: "Enter an email address." };
  const origin = (await headers()).get("origin") ?? "";
  const supabase = await createClient();
  const { error } = await supabase.auth.signInWithOtp({
    email,
    options: { emailRedirectTo: `${origin}/auth/callback` },
  });
  if (error) return { ok: false, message: error.message };
  return { ok: true, message: `Check ${email} for a sign-in link.` };
}

export async function createOrg(_: ActionState, form: FormData): Promise<ActionState> {
  let created: { id: string } | undefined;
  const state = await attempt(async () => {
    created = await api<{ id: string }>("POST", "/v1/orgs", { body: { name: field(form, "name") } });
    return "Organisation created.";
  });
  if (created) redirect(`/o/${created.id}`);
  return state;
}

export async function createProject(_: ActionState, form: FormData): Promise<ActionState> {
  return attempt(async () => {
    await api("POST", "/v1/projects", {
      org: field(form, "org"),
      body: { name: field(form, "name") },
    });
    refresh();
    return "Project created.";
  });
}

export async function setWebhook(_: ActionState, form: FormData): Promise<ActionState> {
  return attempt(async () => {
    const result = await api<{ secret: string }>("PUT", "/v1/orgs/webhook", {
      org: field(form, "org"),
      body: { url: field(form, "url") },
    });
    refresh();
    return { message: "Webhook saved. Copy the signing secret now: it is shown once.", secret: result.secret };
  });
}

export async function createApiKey(_: ActionState, form: FormData): Promise<ActionState> {
  return attempt(async () => {
    const scopes = form.getAll("scopes").filter((s): s is string => typeof s === "string");
    const result = await api<{ key: string }>("POST", "/v1/api-keys", {
      org: field(form, "org"),
      body: { project_id: field(form, "project_id"), name: field(form, "name"), scopes },
    });
    refresh();
    return { message: "API key created. Copy it now: it is shown once.", secret: result.key };
  });
}

export async function revokeApiKey(_: ActionState, form: FormData): Promise<ActionState> {
  return attempt(async () => {
    await api("DELETE", `/v1/api-keys/${encodeURIComponent(field(form, "id"))}`, {
      org: field(form, "org"),
    });
    refresh();
    return "Key revoked.";
  });
}

export async function createWorkload(_: ActionState, form: FormData): Promise<ActionState> {
  return attempt(async () => {
    await api("POST", "/v1/workloads", {
      org: field(form, "org"),
      body: { project_id: field(form, "project_id"), name: field(form, "name") },
    });
    refresh();
    return "Workload created.";
  });
}

export async function calibrate(_: ActionState, form: FormData): Promise<ActionState> {
  return attempt(async () => {
    await api("POST", `/v1/workloads/${encodeURIComponent(field(form, "workload"))}/calibrate`, {
      org: field(form, "org"),
    });
    refresh();
    return "Calibration queued. The new bundle appears below when the worker finishes.";
  });
}

export async function activateBundle(_: ActionState, form: FormData): Promise<ActionState> {
  return attempt(async () => {
    await api("POST", `/v1/bundles/${encodeURIComponent(field(form, "bundle"))}/activate`, {
      org: field(form, "org"),
    });
    refresh();
    return "Bundle activated.";
  });
}

export async function setMode(_: ActionState, form: FormData): Promise<ActionState> {
  return attempt(async () => {
    const mode = field(form, "mode");
    await api("PATCH", `/v1/workloads/${encodeURIComponent(field(form, "workload"))}`, {
      org: field(form, "org"),
      body: { mode },
    });
    refresh();
    return mode === "enforce"
      ? "Switching to enforce: the worker is signing a new bundle version."
      : "Switching to shadow: the worker is signing a new bundle version.";
  });
}

export async function setOverride(_: ActionState, form: FormData): Promise<ActionState> {
  return attempt(async () => {
    const value = field(form, "override");
    await api("POST", `/v1/workloads/${encodeURIComponent(field(form, "workload"))}/override`, {
      org: field(form, "org"),
      body: { override: value === "shadow" ? "shadow" : null },
    });
    refresh();
    return value === "shadow"
      ? "Override set: SDKs stop aborting at their next refresh (≤ 5 minutes)."
      : "Override cleared.";
  });
}
