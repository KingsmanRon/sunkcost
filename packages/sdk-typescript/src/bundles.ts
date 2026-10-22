// Bundle manager: background refresh, verification, rollback protection and disk cache (§4 C2),
// mirroring sunkcost.bundles.
//
// get() is a map read with no I/O, so check() stays network-free. Each registered workload's
// bundle is fetched at start-up and every refreshMs with If-None-Match; failures back off from 5 s
// up to refreshMs. Every accepted bundle raises the workload's version floor, so an older version
// is refused (rollback protection), including across restarts when cacheDir is set. A 404 means no
// active bundle and clears the cached one, which can only reduce enforcement (ADR-0004).

import { mkdirSync, readFileSync, renameSync, rmSync, writeFileSync } from "node:fs";
import { dirname, join } from "node:path";

import { BundleRejected, verifyResponse, type Bundle, type Override, type TrustedKeys } from "./bundle.js";
import type { Transport } from "./transport.js";

export const MIN_RETRY_MS = 5_000;
const SAFE_NAME = /[^A-Za-z0-9._-]/g;

interface Entry {
  bundle: Bundle | null;
  override: Override;
  etag: string | null;
  floor: number; // highest version accepted so far
  failures: number;
  timer: ReturnType<typeof setTimeout> | null;
  inFlight: Promise<boolean> | null;
}

export interface Logger {
  warn(message: string): void;
  debug?(message: string): void;
}

export class BundleManager {
  private readonly entries = new Map<string, Entry>();
  private started = false;
  private stopped = false;
  rejected = 0;
  fetchErrors = 0;

  constructor(
    private readonly transport: Transport | null,
    private readonly keys: TrustedKeys,
    private readonly options: {
      refreshMs?: number;
      cacheDir?: string | null;
      clockUs?: () => number;
      logger?: Logger;
    } = {},
  ) {}

  private get refreshMs(): number {
    return this.options.refreshMs ?? 300_000;
  }

  private nowUs(): number {
    return this.options.clockUs ? this.options.clockUs() : Date.now() * 1000;
  }

  // -- read side (hot path) --------------------------------------------------------------------

  get(workload: string): [Bundle | null, Override] {
    const entry = this.entries.get(workload);
    return entry ? [entry.bundle, entry.override] : [null, null];
  }

  override(workload: string): Override {
    return this.entries.get(workload)?.override ?? null;
  }

  get workloads(): string[] {
    return [...this.entries.keys()];
  }

  // -- registration ----------------------------------------------------------------------------

  /** Start tracking a workload: load its disk cache now, fetch in the background. */
  register(workload: string): void {
    if (this.entries.has(workload)) return;
    const entry: Entry = { bundle: null, override: null, etag: null, floor: 0, failures: 0, timer: null, inFlight: null };
    this.loadCache(workload, entry);
    this.entries.set(workload, entry);
    if (this.started) this.scheduleFetch(workload, 0);
  }

  start(): void {
    if (this.transport === null || this.started) return;
    this.started = true;
    for (const workload of this.entries.keys()) this.scheduleFetch(workload, 0);
  }

  stop(): void {
    this.stopped = true;
    for (const entry of this.entries.values()) {
      if (entry.timer !== null) clearTimeout(entry.timer);
      entry.timer = null;
    }
  }

  private entry(workload: string): Entry {
    const entry = this.entries.get(workload);
    if (!entry) throw new Error(`workload ${workload} is not registered`);
    return entry;
  }

  // -- fetching --------------------------------------------------------------------------------

  private scheduleFetch(workload: string, delayMs: number): void {
    const entry = this.entries.get(workload);
    if (!entry || this.stopped || this.transport === null) return;
    if (entry.timer !== null) clearTimeout(entry.timer);
    entry.timer = setTimeout(() => {
      entry.timer = null;
      void this.refresh(workload);
    }, delayMs);
    entry.timer.unref();
  }

  /** Fetch once now. Resolves true if the workload's state changed. Never rejects. */
  refresh(workload: string): Promise<boolean> {
    this.register(workload);
    const entry = this.entry(workload);
    entry.inFlight ??= this.fetchOnce(workload, entry).finally(() => {
      entry.inFlight = null;
    });
    return entry.inFlight;
  }

  private retry(workload: string, entry: Entry): void {
    entry.failures += 1;
    this.scheduleFetch(workload, Math.min(this.refreshMs, MIN_RETRY_MS * 2 ** (entry.failures - 1)));
  }

  private ok(workload: string, entry: Entry): void {
    entry.failures = 0;
    this.scheduleFetch(workload, this.refreshMs);
  }

  private async fetchOnce(workload: string, entry: Entry): Promise<boolean> {
    if (this.transport === null) return false;
    let result;
    try {
      result = await this.transport.fetchBundle(workload, entry.etag);
    } catch {
      this.fetchErrors += 1;
      this.retry(workload, entry);
      return false;
    }
    if (result.status === 304) {
      this.ok(workload, entry);
      return false;
    }
    if (result.status === 404) {
      const changed = entry.bundle !== null;
      entry.bundle = null;
      entry.override = null;
      entry.etag = null;
      this.ok(workload, entry);
      this.deleteCache(workload);
      return changed;
    }
    if (result.status !== 200) {
      this.fetchErrors += 1;
      this.retry(workload, entry);
      return false;
    }
    let doc: unknown;
    let bundle: Bundle;
    let override: Override;
    try {
      doc = JSON.parse(result.body ?? "");
      [bundle, override] = verifyResponse(doc, this.keys, {
        workload,
        nowUs: this.nowUs(),
        minVersion: entry.floor,
      });
    } catch (error) {
      this.rejected += 1;
      this.options.logger?.warn(`sunkcost: rejected bundle for ${workload}: ${String(error)}`);
      this.retry(workload, entry);
      return false;
    }
    if (bundle.version < entry.floor) return false; // a newer version arrived meanwhile
    entry.bundle = bundle;
    entry.override = override;
    entry.etag = result.etag ?? null;
    entry.floor = Math.max(entry.floor, bundle.version);
    this.ok(workload, entry);
    this.saveCache(workload, entry, doc);
    return true;
  }

  /** Verify and install a bundle response directly (offline use and tests). Throws if rejected. */
  install(workload: string, doc: unknown): void {
    this.register(workload);
    const entry = this.entry(workload);
    const [bundle, override] = verifyResponse(doc, this.keys, {
      workload,
      nowUs: this.nowUs(),
      minVersion: entry.floor,
    });
    entry.bundle = bundle;
    entry.override = override;
    entry.floor = Math.max(entry.floor, bundle.version);
  }

  // -- disk cache (SUNKCOST_CACHE_DIR) ---------------------------------------------------------

  private cachePath(workload: string): string | null {
    const dir = this.options.cacheDir;
    return dir ? join(dir, `bundle-${workload.replace(SAFE_NAME, "_")}.json`) : null;
  }

  private loadCache(workload: string, entry: Entry): void {
    const path = this.cachePath(workload);
    if (path === null) return;
    try {
      const doc = JSON.parse(readFileSync(path, "utf8")) as Record<string, unknown>;
      const floor = typeof doc._sunkcost_floor === "number" ? doc._sunkcost_floor : 0;
      entry.floor = Math.max(entry.floor, floor);
      const [bundle, override] = verifyResponse(doc, this.keys, {
        workload,
        nowUs: this.nowUs(),
        minVersion: entry.floor,
      });
      entry.bundle = bundle;
      entry.override = override;
      entry.floor = Math.max(entry.floor, bundle.version);
    } catch (error) {
      const missing = (error as NodeJS.ErrnoException).code === "ENOENT";
      if (!missing && !(error instanceof BundleRejected && error.reason === "expired")) {
        this.options.logger?.debug?.(`sunkcost: ignoring cached bundle for ${workload}: ${String(error)}`);
      }
    }
  }

  private saveCache(workload: string, entry: Entry, doc: unknown): void {
    const path = this.cachePath(workload);
    if (path === null || typeof doc !== "object" || doc === null) return;
    try {
      mkdirSync(dirname(path), { recursive: true });
      const tmp = `${path}.${String(process.pid)}.tmp`;
      writeFileSync(tmp, JSON.stringify({ ...doc, _sunkcost_floor: entry.floor }));
      renameSync(tmp, path);
    } catch {
      // A cache write failure only costs a fetch at the next start.
    }
  }

  private deleteCache(workload: string): void {
    const path = this.cachePath(workload);
    if (path !== null) rmSync(path, { force: true });
  }
}
