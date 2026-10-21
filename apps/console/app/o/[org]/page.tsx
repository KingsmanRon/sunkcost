import { createProject, setWebhook } from "@/app/actions";
import { ActionForm } from "@/components/ActionForm";
import { isAdmin, org, projects } from "@/lib/data";
import { utc } from "@/lib/format";

export default async function OrgPage({ params }: { params: Promise<{ org: string }> }) {
  const o = await org((await params).org);
  const list = await projects(o.id);
  const admin = isAdmin(o);
  return (
    <>
      <h1>{o.name}</h1>
      <h2>Projects</h2>
      {list.length ? (
        <div className="table-wrap">
          <table>
            <thead>
              <tr>
                <th>Name</th>
                <th>Created</th>
                <th>ID</th>
              </tr>
            </thead>
            <tbody>
              {list.map((p) => (
                <tr key={p.id}>
                  <td>{p.name}</td>
                  <td>{utc(p.created_at)}</td>
                  <td>
                    <code>{p.id}</code>
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      ) : (
        <p className="muted">No projects yet. A project groups workloads and API keys.</p>
      )}
      {admin ? (
        <div className="panel" style={{ marginTop: 12 }}>
          <ActionForm action={createProject} submit="Create project" hidden={{ org: o.id }} inline>
            <label>
              Project name
              <input type="text" name="name" required maxLength={256} />
            </label>
          </ActionForm>
        </div>
      ) : null}
      <h2>Webhook</h2>
      <p className="muted">
        Sunkcost posts signed events (for example <code>workload.downgraded</code>) here.
        Current URL: {o.webhook_url ? <code>{o.webhook_url}</code> : "none"}.
      </p>
      {admin ? (
        <div className="panel">
          <ActionForm action={setWebhook} submit="Save webhook" hidden={{ org: o.id }} inline>
            <label>
              HTTPS URL
              <input type="url" name="url" required pattern="https://.*" placeholder="https://" />
            </label>
          </ActionForm>
        </div>
      ) : null}
    </>
  );
}
