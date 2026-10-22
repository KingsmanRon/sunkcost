// USD amounts as exact decimals (CLAUDE.md, §6.1): bigint nano-dollars inside the SDK, decimal
// strings on the wire, never floats. Mirrors sunkcost._money.

export type Nanos = bigint;

const SCALE = 9n;
const ONE = 10n ** SCALE;
/** numeric(20,9): at most 11 integer digits. */
export const MAX_MONEY: Nanos = 99_999_999_999_999_999_999n;
const DECIMAL = /^(\d+)?(?:\.(\d*))?(?:[eE]([+-]?\d+))?$/;

/** Round a / b half to even (b > 0, a ≥ 0). */
export function divHalfEven(a: bigint, b: bigint): bigint {
  const q = a / b;
  const r = a % b;
  const twice = 2n * r;
  if (twice > b || (twice === b && q % 2n === 1n)) return q + 1n;
  return q;
}

/** A non-negative decimal string (exponent allowed) as nanos, rounded half-even; else null. */
export function parseMoney(text: string): Nanos | null {
  const m = DECIMAL.exec(text.trim());
  if (!m || (m[1] === undefined && (m[2] === undefined || m[2] === ""))) return null;
  const digits = (m[1] ?? "") + (m[2] ?? "");
  const exponent = Number(m[3] ?? "0") - (m[2] ?? "").length + Number(SCALE);
  if (!Number.isSafeInteger(exponent) || Math.abs(exponent) > 400) return null;
  let value = BigInt(digits || "0");
  if (exponent >= 0) {
    value *= 10n ** BigInt(exponent);
  } else {
    value = divHalfEven(value, 10n ** BigInt(-exponent));
  }
  return value;
}

/**
 * A caller-supplied USD amount as nanos, or null when it is not a valid amount. Accepts decimal
 * strings, numbers and bigint whole dollars. A number is taken at its shortest round-trip decimal
 * form (String(x)), so 0.1 means 0.1, not 0.1000000000000000055….
 */
export function toMoney(value: unknown): Nanos | null {
  let nanos: Nanos | null;
  if (typeof value === "number") {
    if (!Number.isFinite(value) || value < 0) return null;
    nanos = parseMoney(String(value));
  } else if (typeof value === "string") {
    nanos = parseMoney(value);
  } else if (typeof value === "bigint") {
    nanos = value >= 0n ? value * ONE : null; // a bigint is whole dollars
  } else {
    return null;
  }
  return nanos !== null && nanos <= MAX_MONEY ? nanos : null;
}

/** Wire form: plain decimal, no exponent, at most 9 fractional digits, no trailing zeros. */
export function moneyStr(nanos: Nanos): string {
  const whole = nanos / ONE;
  const frac = (nanos % ONE).toString().padStart(Number(SCALE), "0").replace(/0+$/, "");
  return frac ? `${whole.toString()}.${frac}` : whole.toString();
}
