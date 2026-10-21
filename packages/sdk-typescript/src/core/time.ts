// UTC RFC 3339 timestamps as integer microseconds since the epoch (mirrors sunkcost.core.timeutil).
// Wire timestamps end in a literal "Z" with 0-9 fractional digits; digits past the sixth are
// truncated. Microseconds stay below 2^53 until the year 2255, so a number holds them exactly.

const TIMESTAMP =
  /^([0-9]{4})-([0-9]{2})-([0-9]{2})T([0-9]{2}):([0-9]{2}):([0-9]{2})(?:\.([0-9]{1,9}))?Z$/;
const US_PER_DAY = 86_400_000_000;

function daysFromCivil(y: number, m: number, d: number): number {
  // Howard Hinnant's algorithm: days since 1970-01-01 in the proleptic Gregorian calendar.
  const yy = m <= 2 ? y - 1 : y;
  const era = Math.floor(yy / 400);
  const yoe = yy - era * 400;
  const doy = Math.floor((153 * (m + (m > 2 ? -3 : 9)) + 2) / 5) + d - 1;
  const doe = yoe * 365 + Math.floor(yoe / 4) - Math.floor(yoe / 100) + doy;
  return era * 146097 + doe - 719468;
}

function civilFromDays(z: number): [number, number, number] {
  const zz = z + 719468;
  const era = Math.floor(zz / 146097);
  const doe = zz - era * 146097;
  const yoe = Math.floor((doe - Math.floor(doe / 1460) + Math.floor(doe / 36524) - Math.floor(doe / 146096)) / 365);
  const doy = doe - (365 * yoe + Math.floor(yoe / 4) - Math.floor(yoe / 100));
  const mp = Math.floor((5 * doy + 2) / 153);
  const d = doy - Math.floor((153 * mp + 2) / 5) + 1;
  const m = mp + (mp < 10 ? 3 : -9);
  return [yoe + era * 400 + (m <= 2 ? 1 : 0), m, d];
}

function daysInMonth(y: number, m: number): number {
  if (m === 2) return (y % 4 === 0 && y % 100 !== 0) || y % 400 === 0 ? 29 : 28;
  return [4, 6, 9, 11].includes(m) ? 30 : 31;
}

/** Parse `YYYY-MM-DDTHH:MM:SS[.fraction]Z` to microseconds since the epoch. */
export function parseTimestampUs(value: string): number {
  const m = TIMESTAMP.exec(value);
  if (!m) throw new RangeError(`not a UTC RFC 3339 timestamp: ${JSON.stringify(value)}`);
  const [year, month, day, hour, minute, second] = m.slice(1, 7).map(Number) as [
    number, number, number, number, number, number,
  ];
  if (hour > 23 || minute > 59 || second > 59) {
    throw new RangeError(`time of day out of range in timestamp ${JSON.stringify(value)}`);
  }
  if (year < 1 || month < 1 || month > 12 || day < 1 || day > daysInMonth(year, month)) {
    throw new RangeError(`invalid calendar date in timestamp ${JSON.stringify(value)}`);
  }
  const fraction = Number((m[7] ?? "").padEnd(6, "0").slice(0, 6));
  return (
    daysFromCivil(year, month, day) * US_PER_DAY +
    ((hour * 60 + minute) * 60 + second) * 1_000_000 +
    fraction
  );
}

const pad = (n: number, width = 2): string => String(n).padStart(width, "0");

/** Format microseconds since the epoch as `YYYY-MM-DDTHH:MM:SS.ffffffZ`. */
export function formatTimestampUs(us: number): string {
  const days = Math.floor(us / US_PER_DAY);
  const rem = us - days * US_PER_DAY;
  const [y, mo, d] = civilFromDays(days);
  const seconds = Math.floor(rem / 1_000_000);
  const micros = rem - seconds * 1_000_000;
  const hour = Math.floor(seconds / 3600);
  const minute = Math.floor((seconds % 3600) / 60);
  const second = seconds % 60;
  return `${pad(y, 4)}-${pad(mo)}-${pad(d)}T${pad(hour)}:${pad(minute)}:${pad(second)}.${pad(micros, 6)}Z`;
}

/** The UTC calendar date of a timestamp, as `YYYY-MM-DD`. */
export function utcDate(us: number): string {
  return formatTimestampUs(us).slice(0, 10);
}
