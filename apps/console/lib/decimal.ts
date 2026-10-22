// Money and rates arrive as decimal strings (numeric(20,9) cast to text) and are never parsed into
// floats (CLAUDE.md). Arithmetic is on integers scaled by 10^9.

const SCALE = 9;
const ONE = 10n ** BigInt(SCALE);
const DECIMAL = /^(-)?(\d+)(?:\.(\d+))?$/;

/** Parse a decimal string into an integer of nano-units. Extra digits beyond 9 are truncated. */
export function parseDecimal(text: string | null | undefined): bigint {
  if (text === null || text === undefined || text === "") return 0n;
  const match = DECIMAL.exec(text.trim());
  if (!match) throw new Error(`not a decimal: ${text}`);
  const [, sign, whole = "0", frac = ""] = match;
  const value = BigInt(whole) * ONE + BigInt((frac + "0".repeat(SCALE)).slice(0, SCALE));
  return sign ? -value : value;
}

export function sumDecimals(values: readonly (string | null | undefined)[]): bigint {
  return values.reduce((acc, v) => acc + parseDecimal(v), 0n);
}

/** "$1,234.57": rounded half away from zero to cents (or to `digits` places). */
export function formatUsd(nanos: bigint, digits = 2): string {
  const negative = nanos < 0n;
  const abs = negative ? -nanos : nanos;
  const unit = 10n ** BigInt(SCALE - digits);
  const rounded = (abs + unit / 2n) / unit;
  const base = 10n ** BigInt(digits);
  const whole = (rounded / base).toString().replace(/\B(?=(\d{3})+(?!\d))/g, ",");
  const frac = digits > 0 ? "." + (rounded % base).toString().padStart(digits, "0") : "";
  return `${negative && rounded !== 0n ? "-" : ""}$${whole}${frac}`;
}

/** value / max as a fraction of `size` pixels, without floating-point money arithmetic. */
export function scaleTo(value: bigint, max: bigint, size: number): number {
  if (max <= 0n || value <= 0n) return 0;
  return Number((value * BigInt(Math.round(size * 1000))) / max) / 1000;
}
