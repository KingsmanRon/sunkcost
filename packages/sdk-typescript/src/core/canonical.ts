// Canonical JSON (RFC 8785), mirroring sunkcost.core.canonical.
//
// canonicalJson(value) is the form bundles are signed over: objects, arrays, strings, integers
// within ±(2^53 − 1), booleans and null. Non-integral numbers are refused, because signed content
// carries them as decimal strings. canonicalJson(value, { allowFloats: true }) adds RFC 8785's
// number serialisation, which is ECMAScript's Number::toString; tool arguments are hashed that way.

export class CanonicalJSONError extends Error {
  override name = "CanonicalJSONError";
}

const encoder = new TextEncoder();

export function canonicalJson(value: unknown, options: { allowFloats?: boolean } = {}): Uint8Array {
  const parts: string[] = [];
  emit(value, options.allowFloats ?? false, parts);
  return encoder.encode(parts.join(""));
}

export function canonicalJsonText(value: unknown, options: { allowFloats?: boolean } = {}): string {
  const parts: string[] = [];
  emit(value, options.allowFloats ?? false, parts);
  return parts.join("");
}

function emit(value: unknown, allowFloats: boolean, out: string[]): void {
  if (value === null) {
    out.push("null");
  } else if (value === true || value === false) {
    out.push(value ? "true" : "false");
  } else if (typeof value === "number") {
    out.push(number(value, allowFloats));
  } else if (typeof value === "string") {
    out.push(string(value));
  } else if (Array.isArray(value)) {
    out.push("[");
    value.forEach((item: unknown, i) => {
      if (i) out.push(",");
      emit(item, allowFloats, out);
    });
    out.push("]");
  } else if (typeof value === "object" && isPlainObject(value)) {
    const record = value as Record<string, unknown>;
    out.push("{");
    // RFC 8785 section 3.2.3: sort by UTF-16 code units, which is JavaScript's default order.
    Object.keys(record)
      .sort()
      .forEach((key, i) => {
        if (i) out.push(",");
        out.push(string(key), ":");
        emit(record[key], allowFloats, out);
      });
    out.push("}");
  } else {
    throw new CanonicalJSONError(`unsupported type ${value === undefined ? "undefined" : typeof value}`);
  }
}

function isPlainObject(value: object): boolean {
  const proto: unknown = Object.getPrototypeOf(value);
  return proto === Object.prototype || proto === null;
}

function number(x: number, allowFloats: boolean): string {
  if (!Number.isFinite(x)) throw new CanonicalJSONError("NaN and Infinity have no JSON form");
  if (Number.isInteger(x) && Math.abs(x) <= Number.MAX_SAFE_INTEGER) return String(x); // "-0" → "0"
  if (!allowFloats) {
    throw new CanonicalJSONError(
      Number.isInteger(x)
        ? "integer outside the IEEE-754 safe range"
        : "floats are not allowed in signed content",
    );
  }
  return String(x);
}

const LONE_SURROGATE = /[\uD800-\uDBFF](?![\uDC00-\uDFFF])|(?<![\uD800-\uDBFF])[\uDC00-\uDFFF]/;

function string(value: string): string {
  if (LONE_SURROGATE.test(value)) throw new CanonicalJSONError("unpaired surrogate");
  // JSON.stringify escapes exactly what RFC 8785 requires: '"', '\\', \b \f \n \r \t, and every
  // other U+0000-U+001F as lower-case \u00xx.
  return JSON.stringify(value);
}
