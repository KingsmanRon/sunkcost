import { NextResponse, type NextRequest } from "next/server";

import { requestOrigin } from "@/lib/origin";
import { createClient } from "@/lib/supabase/server";

/** The magic link lands here with a PKCE code, which becomes the session cookies. */
export async function GET(request: NextRequest) {
  const url = request.nextUrl;
  const code = url.searchParams.get("code");
  if (code) {
    const supabase = await createClient();
    const { error } = await supabase.auth.exchangeCodeForSession(code);
    if (!error) return NextResponse.redirect(new URL("/", requestOrigin(request)));
  }
  return NextResponse.redirect(new URL("/login?error=link", requestOrigin(request)));
}
