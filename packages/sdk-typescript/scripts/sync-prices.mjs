// Copy packages/spec/prices/prices.v1.json into src/data/prices.ts (the SDK ships it compiled in).
import { readFileSync, writeFileSync } from "node:fs";

const spec = new URL("../../spec/prices/prices.v1.json", import.meta.url);
const out = new URL("../src/data/prices.ts", import.meta.url);
const doc = JSON.parse(readFileSync(spec, "utf8"));
writeFileSync(
  out,
  "// Generated from packages/spec/prices/prices.v1.json; test/prices.test.ts fails if they differ.\n" +
    "// Regenerate with: node scripts/sync-prices.mjs\n\n" +
    `export const BUNDLED_PRICES = ${JSON.stringify(doc, null, 2)} as const;\n`,
);
