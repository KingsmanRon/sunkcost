import type { NextRequest } from "next/server";

/**
 * The origin the browser used. `request.nextUrl` reflects the server's bind address (for example
 * localhost when the browser used 127.0.0.1), and a redirect there would drop the session cookie.
 */
export function requestOrigin(request: NextRequest): string {
  const host = request.headers.get("x-forwarded-host") ?? request.headers.get("host");
  if (!host) return request.nextUrl.origin;
  const proto =
    request.headers.get("x-forwarded-proto") ?? request.nextUrl.protocol.replace(/:$/, "");
  return `${proto}://${host}`;
}
