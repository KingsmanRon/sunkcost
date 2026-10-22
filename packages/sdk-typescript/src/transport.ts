// HTTP transport for the two SDK → API calls (§5.1 ① and ②), on the platform fetch. Never used
// inside check(). Mirrors sunkcost.transport.

import { gzipSync } from "node:zlib";

import { SDK_NAME, SDK_VERSION } from "./version.js";

export const GZIP_MIN_BYTES = 1024;

export type SendStatus = "ok" | "retry" | "drop";

export interface SendResult {
  status: SendStatus;
  retryAfterS?: number | null;
  httpStatus?: number | null;
}

/** status is 200 (new body), 304 (unchanged), 404 (no active bundle) or another code. */
export interface FetchResult {
  status: number;
  body?: string;
  etag?: string | null;
}

export interface Transport {
  sendBatch(body: Uint8Array): Promise<SendResult>;
  fetchBundle(workload: string, etag: string | null): Promise<FetchResult>;
  close(): void;
}

/** 2xx accepted; 408, 425, 429 and 5xx retried; any other code is permanent (batch dropped). */
export function classify(status: number): SendStatus {
  if (status >= 200 && status < 300) return "ok";
  if (status === 408 || status === 425 || status === 429 || status >= 500) return "retry";
  return "drop";
}

function retryAfter(response: Response): number | null {
  const value = response.headers.get("retry-after");
  if (value === null) return null;
  const n = Number(value);
  return Number.isFinite(n) ? Math.max(0, n) : null;
}

export class HttpTransport implements Transport {
  private readonly endpoint: string;
  private readonly headers: Record<string, string>;
  private readonly controller = new AbortController();

  constructor(
    endpoint: string,
    apiKey: string,
    private readonly timeoutMs = 10_000,
  ) {
    this.endpoint = endpoint.replace(/\/+$/, "");
    this.headers = { authorization: `Bearer ${apiKey}`, "user-agent": `${SDK_NAME}/${SDK_VERSION}` };
  }

  private signal(): AbortSignal {
    return AbortSignal.any([this.controller.signal, AbortSignal.timeout(this.timeoutMs)]);
  }

  async sendBatch(body: Uint8Array): Promise<SendResult> {
    const headers: Record<string, string> = { ...this.headers, "content-type": "application/json" };
    let payload = body;
    if (body.length >= GZIP_MIN_BYTES) {
      payload = gzipSync(body, { level: 5 });
      headers["content-encoding"] = "gzip";
    }
    try {
      const response = await fetch(`${this.endpoint}/v1/ingest`, {
        method: "POST",
        headers,
        body: payload,
        redirect: "manual",
        signal: this.signal(),
      });
      await response.arrayBuffer(); // release the connection
      return { status: classify(response.status), retryAfterS: retryAfter(response), httpStatus: response.status };
    } catch {
      return { status: "retry" };
    }
  }

  async fetchBundle(workload: string, etag: string | null): Promise<FetchResult> {
    const headers: Record<string, string> = { ...this.headers };
    if (etag) headers["if-none-match"] = etag;
    const url = `${this.endpoint}/v1/bundles/active?workload=${encodeURIComponent(workload)}`;
    const response = await fetch(url, { headers, redirect: "manual", signal: this.signal() });
    const body = await response.text();
    return { status: response.status, body, etag: response.headers.get("etag") };
  }

  close(): void {
    this.controller.abort();
  }
}
