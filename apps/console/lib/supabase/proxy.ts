import { createServerClient } from "@supabase/ssr";
import { NextResponse, type NextRequest } from "next/server";

import { env } from "@/lib/env";
import { requestOrigin } from "@/lib/origin";

const PUBLIC_PATHS = ["/login", "/auth/"];

/** Refresh the Supabase session cookies on every request and send signed-out users to /login. */
export async function updateSession(request: NextRequest): Promise<NextResponse> {
  let response = NextResponse.next({ request });
  const supabase = createServerClient(env.supabaseUrl, env.supabaseAnonKey, {
    cookies: {
      getAll: () => request.cookies.getAll(),
      setAll: (toSet, headers) => {
        for (const { name, value } of toSet) request.cookies.set(name, value);
        response = NextResponse.next({ request });
        for (const { name, value, options } of toSet) response.cookies.set(name, value, options);
        for (const [key, value] of Object.entries(headers)) response.headers.set(key, value);
      },
    },
  });
  // getClaims() verifies the access token (refreshing it when needed); never trust getSession().
  const { data } = await supabase.auth.getClaims();
  const path = request.nextUrl.pathname;
  if (!data && !PUBLIC_PATHS.some((p) => path === p || path.startsWith(p))) {
    const redirect = NextResponse.redirect(new URL("/login", requestOrigin(request)));
    for (const cookie of response.cookies.getAll()) redirect.cookies.set(cookie);
    return redirect;
  }
  return response;
}
