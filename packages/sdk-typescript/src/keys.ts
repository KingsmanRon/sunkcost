// Default trusted bundle-signing keys, pinned by key_id (§9). Empty until the owner generates the
// production key offline (scripts/gen-signing-key.py) and adds its public half here and to the
// Python SDK before the first release. The public RFC 8032 test key must never be listed here;
// test/client.test.ts enforces that.

export const DEFAULT_TRUSTED_KEYS: Readonly<Record<string, string>> = Object.freeze({});
