import type { Metadata } from "next";
import Link from "next/link";
import type { ReactNode } from "react";

import { currentUser } from "@/lib/supabase/server";

import "./globals.css";

export const metadata: Metadata = {
  title: "Sunkcost",
  description: "Stop agent runs that are going to fail.",
};

export default async function RootLayout({ children }: { children: ReactNode }) {
  const user = await currentUser();
  return (
    <html lang="en">
      <body>
        <header className="topbar">
          <Link href="/" className="brand">
            Sunkcost
          </Link>
          {user ? (
            <nav className="account">
              <Link href="/orgs">Organisations</Link>
              <span className="muted">{user.email}</span>
              <form action="/auth/signout" method="post">
                <button type="submit" className="link">
                  Sign out
                </button>
              </form>
            </nav>
          ) : null}
        </header>
        <main>{children}</main>
      </body>
    </html>
  );
}
