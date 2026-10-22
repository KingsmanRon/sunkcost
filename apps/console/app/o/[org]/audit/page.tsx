import { audit, org } from "@/lib/data";
import { utc } from "@/lib/format";

function summary(value: unknown): string {
  if (value === null || value === undefined) return "";
  const text = JSON.stringify(value);
  return text.length > 160 ? `${text.slice(0, 157)}…` : text;
}

export default async function AuditPage({ params }: { params: Promise<{ org: string }> }) {
  const o = await org((await params).org);
  const entries = await audit(o.id);
  return (
    <>
      <h1>Audit log</h1>
      <p className="muted">Every change, by a person or by the system. Entries can’t be edited.</p>
      <div className="table-wrap">
        <table>
          <thead>
            <tr>
              <th>When</th>
              <th>Actor</th>
              <th>Action</th>
              <th>Target</th>
              <th>Change</th>
            </tr>
          </thead>
          <tbody>
            {entries.map((e) => (
              <tr key={e.id}>
                <td>{utc(e.at)}</td>
                <td>
                  <code>{e.actor}</code>
                </td>
                <td>{e.action}</td>
                <td>
                  <code>{e.target}</code>
                </td>
                <td>
                  <code className="muted">{summary(e.after)}</code>
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    </>
  );
}
