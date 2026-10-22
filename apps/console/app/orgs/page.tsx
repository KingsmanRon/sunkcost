import Link from "next/link";

import { createOrg } from "@/app/actions";
import { ActionForm } from "@/components/ActionForm";
import { myOrgs } from "@/lib/data";

export default async function OrgsPage() {
  const orgs = await myOrgs();
  return (
    <>
      <h1>Organisations</h1>
      {orgs.length ? (
        <ul>
          {orgs.map((o) => (
            <li key={o.id}>
              <Link href={`/o/${o.id}`}>{o.name}</Link> <span className="muted">({o.role})</span>
            </li>
          ))}
        </ul>
      ) : (
        <p className="muted">You don’t belong to an organisation yet. Create one to start.</p>
      )}
      <h2>New organisation</h2>
      <div className="panel">
        <ActionForm action={createOrg} submit="Create organisation">
          <label>
            Name
            <input type="text" name="name" required maxLength={256} />
          </label>
        </ActionForm>
      </div>
    </>
  );
}
