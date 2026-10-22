// Sunkcost TypeScript SDK (§4 C3): stop agent runs that are going to fail, with a decision engine
// that runs in-process against a signed policy bundle and never touches the network in check().

export { Run, Sunkcost, type RunStatus, type Stats, type StepInput, type SunkcostOptions } from "./client.js";
export { Decision } from "./decision.js";
export { RunAborted, findAbort } from "./errors.js";
export { BundleRejected, type Bundle } from "./bundle.js";
export { DEFAULT_TRUSTED_KEYS } from "./keys.js";
export { PriceTable } from "./prices.js";
export type { FetchResult, SendResult, Transport } from "./transport.js";
export { SDK_NAME, SDK_VERSION } from "./version.js";
export * from "./wire.js";
export * as core from "./core/index.js";

export const SCHEMA_VERSION = "1";
