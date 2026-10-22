import { readdirSync, readFileSync } from "node:fs";

export const SPEC = new URL("../../spec/", import.meta.url);

export function specJson(path: string): unknown {
  return JSON.parse(readFileSync(new URL(path, SPEC), "utf8"));
}

export function specFiles(dir: string): string[] {
  return readdirSync(new URL(dir, SPEC))
    .filter((f) => f.endsWith(".json"))
    .sort()
    .map((f) => `${dir}${f}`);
}

export function hex(bytes: Uint8Array): string {
  return Buffer.from(bytes).toString("hex");
}

export function closeTo(actual: number, expected: number, rel = 1e-12): boolean {
  if (actual === expected) return true;
  return Math.abs(actual - expected) <= rel * Math.max(Math.abs(expected), 1e-300);
}
