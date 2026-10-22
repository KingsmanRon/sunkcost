// JSON Schema validation against packages/spec/schemas (2020-12), as the Python tests do.

import { readdirSync, readFileSync } from "node:fs";

import { Ajv2020 } from "ajv/dist/2020.js";
import addFormats from "ajv-formats";

import { SPEC } from "./helpers.js";

const ajv = new Ajv2020({ allErrors: true, strict: false });
addFormats.default(ajv);
const dir = new URL("schemas/", SPEC);
for (const file of readdirSync(dir).filter((f) => f.endsWith(".json"))) {
  ajv.addSchema(JSON.parse(readFileSync(new URL(file, dir), "utf8")) as object);
}

/** Validation errors for `doc` against the schema whose file is `<name>.json` (e.g. "ingest-batch.v1"). */
export function schemaErrors(name: string, doc: unknown): string[] {
  const id = findByFile(name);
  if (!id) throw new Error(`no schema ${name}`);
  return id(doc) ? [] : (id.errors ?? []).map((e) => `${e.instancePath} ${e.message ?? ""}`);
}

function findByFile(name: string) {
  const schema = JSON.parse(readFileSync(new URL(`${name}.json`, dir), "utf8")) as { $id: string };
  return ajv.getSchema(schema.$id);
}
