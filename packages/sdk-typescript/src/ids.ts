// UUIDv7 run IDs (RFC 9562 §5.7), generated client-side (§2). Mirrors sunkcost._ids.

import { randomBytes } from "node:crypto";

export const UUID7 = /^[0-9a-f]{8}-[0-9a-f]{4}-7[0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$/;
export const UUID = /^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/;

/** 48-bit Unix milliseconds, version 7, 74 random bits, RFC 4122 variant; lower-case. */
export function uuid7(unixMs: number = Date.now()): string {
  const bytes = randomBytes(16);
  let ms = BigInt(Math.floor(unixMs)) & 0xffff_ffff_ffffn;
  for (let i = 5; i >= 0; i--) {
    bytes[i] = Number(ms & 0xffn);
    ms >>= 8n;
  }
  bytes[6] = 0x70 | ((bytes[6] ?? 0) & 0x0f);
  bytes[8] = 0x80 | ((bytes[8] ?? 0) & 0x3f);
  const h = bytes.toString("hex");
  return `${h.slice(0, 8)}-${h.slice(8, 12)}-${h.slice(12, 16)}-${h.slice(16, 20)}-${h.slice(20)}`;
}

export function isUuid7(value: unknown): value is string {
  return typeof value === "string" && UUID7.test(value);
}
