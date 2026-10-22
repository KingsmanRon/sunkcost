import type { ReactNode } from "react";

import { Tabs } from "@/components/Tabs";
import { org } from "@/lib/data";

export default async function OrgLayout({
  children,
  params,
}: {
  children: ReactNode;
  params: Promise<{ org: string }>;
}) {
  const o = await org((await params).org);
  return (
    <>
      <p className="muted" style={{ margin: 0 }}>
        {o.name} · {o.role}
      </p>
      <Tabs base={`/o/${o.id}`} />
      {children}
    </>
  );
}
