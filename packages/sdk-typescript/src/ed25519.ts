// Ed25519 via @noble/ed25519 (§4 C2/C3), with SHA-512 from node:crypto so verification is
// synchronous. Verification is strict RFC 8032 (zip215: false), like the OpenSSL-backed Python SDK.

import { createHash } from "node:crypto";

import * as ed from "@noble/ed25519";

ed.hashes.sha512 = (message: Uint8Array) => new Uint8Array(createHash("sha512").update(message).digest());

export function ed25519Verify(signature: Uint8Array, message: Uint8Array, publicKey: Uint8Array): boolean {
  try {
    return ed.verify(signature, message, publicKey, { zip215: false });
  } catch {
    return false;
  }
}

/** For tests and tooling only: the SDK never holds a signing key. */
export function ed25519Sign(message: Uint8Array, seed: Uint8Array): Uint8Array {
  return ed.sign(message, seed);
}

export function ed25519PublicKey(seed: Uint8Array): Uint8Array {
  return ed.getPublicKey(seed);
}
