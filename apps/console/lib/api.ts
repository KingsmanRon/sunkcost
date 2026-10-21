import "server-only";

import type { ActionState } from "@/lib/action-state";
import { env } from "@/lib/env";
import { problemMessage } from "@/lib/problem";
import { createClient } from "@/lib/supabase/server";

export class ApiError extends Error {
  constructor(
    readonly status: number,
    message: string,
  ) {
    super(message);
  }
}

/**
 * Call the Sunkcost API as the signed-in user (§4 C9: the console never writes to Postgres).
 * The API verifies the access token itself, so an expired or forged cookie gets a 401 there.
 */
export async function api<T>(
  method: "POST" | "PATCH" | "PUT" | "DELETE",
  path: string,
  { org, body }: { org?: string; body?: unknown } = {},
): Promise<T> {
  const supabase = await createClient();
  const { data } = await supabase.auth.getSession();
  const token = data.session?.access_token;
  if (!token) throw new ApiError(401, "Your session has expired. Sign in again.");
  const headers: Record<string, string> = { authorization: `Bearer ${token}` };
  if (org) headers["x-sunkcost-org"] = org;
  if (body !== undefined) headers["content-type"] = "application/json";
  const response = await fetch(`${env.apiUrl}${path}`, {
    method,
    headers,
    body: body === undefined ? null : JSON.stringify(body),
    cache: "no-store",
  });
  const text = await response.text();
  const parsed: unknown = text ? JSON.parse(text) : {};
  if (!response.ok) throw new ApiError(response.status, problemMessage(parsed, response.status));
  return parsed as T;
}

/** Run an API mutation for a form, turning failures into a message instead of an error page. */
export async function attempt(
  run: () => Promise<Omit<ActionState, "ok"> | string>,
): Promise<ActionState> {
  try {
    const result = await run();
    return typeof result === "string" ? { ok: true, message: result } : { ok: true, ...result };
  } catch (error) {
    if (error instanceof ApiError) return { ok: false, message: error.message };
    throw error;
  }
}
