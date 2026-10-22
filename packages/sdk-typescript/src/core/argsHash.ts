// Tool-argument hashing: the only form in which arguments leave the process (§9, ADR-0002).

import { createHmac } from "node:crypto";

import { canonicalJson } from "./canonical.js";

export const ARGS_HASH_PREFIX = "hmac-sha256:";

/** "hmac-sha256:" + hex HMAC-SHA256 keyed with the salt over JCS(args), floats allowed. */
export function argsHash(salt: string | Uint8Array, args: unknown): string {
  const key = typeof salt === "string" ? Buffer.from(salt, "utf8") : salt;
  const digest = createHmac("sha256", key)
    .update(canonicalJson(args, { allowFloats: true }))
    .digest("hex");
  return ARGS_HASH_PREFIX + digest;
}
