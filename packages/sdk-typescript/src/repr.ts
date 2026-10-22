// Python's repr() of a float64: the shortest round-trip digits, fixed notation for exponents in
// [-4, 16) with a trailing ".0" when integral, otherwise "d.ddde±XX". Scores and thresholds are
// written this way on the wire (decision.v1), so both SDKs send identical strings.

export function pyFloatRepr(x: number): string {
  if (Number.isNaN(x)) return "nan";
  if (!Number.isFinite(x)) return x > 0 ? "inf" : "-inf";
  if (x === 0) return Object.is(x, -0) ? "-0.0" : "0.0";
  const sign = x < 0 ? "-" : "";
  // toExponential() without an argument gives the shortest round-trip digits.
  const [mantissa = "", expText = "0"] = Math.abs(x).toExponential().split("e");
  const digits = mantissa.replace(".", "");
  const exp = Number(expText);
  if (exp >= -4 && exp < 16) {
    if (exp < 0) return `${sign}0.${"0".repeat(-exp - 1)}${digits}`;
    const intPart = digits.slice(0, exp + 1).padEnd(exp + 1, "0");
    const fracPart = digits.slice(exp + 1);
    return `${sign}${intPart}.${fracPart || "0"}`;
  }
  const m = digits.length > 1 ? `${digits[0] ?? ""}.${digits.slice(1)}` : digits;
  const e = Math.abs(exp).toString().padStart(2, "0");
  return `${sign}${m}e${exp < 0 ? "-" : "+"}${e}`;
}
