import { createApiKey, revokeApiKey } from "@/app/actions";
import { ActionForm } from "@/components/ActionForm";
import { apiKeys, isAdmin, org, projects } from "@/lib/data";
import { utc } from "@/lib/format";

const SCOPES = ["ingest", "outcome:write", "bundle:read"] as const;

export default async function KeysPage({ params }: { params: Promise<{ org: string }> }) {
  const o = await org((await params).org);
  const [keys, list] = await Promise.all([apiKeys(o.id), projects(o.id)]);
  const names = new Map(list.map((p) => [p.id, p.name]));
  const admin = isAdmin(o);
  return (
    <>
      <h1>API keys</h1>
      <p className="muted">
        SDKs authenticate with a project key. A revoked key is refused on its next request.
      </p>
      {admin && list.length ? (
        <div className="panel">
          <ActionForm action={createApiKey} submit="Create key" hidden={{ org: o.id }}>
            <div className="row">
              <label>
                Project
                <select name="project_id" required>
                  {list.map((p) => (
                    <option key={p.id} value={p.id}>
                      {p.name}
                    </option>
                  ))}
                </select>
              </label>
              <label>
                Name
                <input type="text" name="name" maxLength={256} placeholder="production agents" />
              </label>
            </div>
            <div className="row">
              {SCOPES.map((s) => (
                <label key={s} className="check">
                  <input type="checkbox" name="scopes" value={s} defaultChecked />
                  {s}
                </label>
              ))}
            </div>
          </ActionForm>
        </div>
      ) : null}
      {admin && !list.length ? <p className="muted">Create a project first.</p> : null}
      <h2>Keys</h2>
      {keys.length ? (
        <div className="table-wrap">
          <table>
            <thead>
              <tr>
                <th>Name</th>
                <th>Prefix</th>
                <th>Project</th>
                <th>Scopes</th>
                <th>Created</th>
                <th>Last used</th>
                <th>Status</th>
              </tr>
            </thead>
            <tbody>
              {keys.map((k) => (
                <tr key={k.id}>
                  <td>{k.name || "—"}</td>
                  <td>
                    <code>{k.prefix}</code>
                  </td>
                  <td>{names.get(k.project_id) ?? k.project_id}</td>
                  <td>{k.scopes.join(", ")}</td>
                  <td>{utc(k.created_at)}</td>
                  <td>{utc(k.last_used_at)}</td>
                  <td>
                    {k.revoked_at ? (
                      <span className="badge rejected">revoked {utc(k.revoked_at)}</span>
                    ) : admin ? (
                      <ActionForm
                        action={revokeApiKey}
                        submit="Revoke"
                        hidden={{ org: o.id, id: k.id }}
                        variant="danger"
                        confirm={`Revoke key ${k.prefix}? Agents using it stop sending data.`}
                      />
                    ) : (
                      <span className="badge active">active</span>
                    )}
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      ) : (
        <p className="muted">No keys yet.</p>
      )}
    </>
  );
}
