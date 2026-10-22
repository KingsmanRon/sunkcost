import { redirect } from "next/navigation";

import { signIn } from "@/app/actions";
import { ActionForm } from "@/components/ActionForm";
import { currentUser } from "@/lib/supabase/server";

export default async function LoginPage({
  searchParams,
}: {
  searchParams: Promise<{ error?: string }>;
}) {
  if (await currentUser()) redirect("/");
  const { error } = await searchParams;
  return (
    <section className="panel" style={{ maxWidth: 420, margin: "48px auto" }}>
      <h1>Sign in</h1>
      <p className="muted">We email you a one-time sign-in link.</p>
      {error ? <p className="error">That link is invalid or has expired. Request a new one.</p> : null}
      <ActionForm action={signIn} submit="Email me a link">
        <label>
          Email
          <input type="email" name="email" required autoComplete="email" />
        </label>
      </ActionForm>
    </section>
  );
}
