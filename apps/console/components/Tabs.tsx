"use client";

import Link from "next/link";
import { usePathname } from "next/navigation";

export function Tabs({ base }: { base: string }) {
  const path = usePathname();
  const tabs = [
    { href: base, label: "Overview", exact: true },
    { href: `${base}/workloads`, label: "Workloads" },
    { href: `${base}/keys`, label: "API keys" },
    { href: `${base}/audit`, label: "Audit log" },
  ];
  return (
    <nav className="tabs" aria-label="Organisation">
      {tabs.map((t) => {
        const current = t.exact ? path === t.href : path.startsWith(t.href);
        return (
          <Link key={t.href} href={t.href} aria-current={current ? "page" : undefined}>
            {t.label}
          </Link>
        );
      })}
    </nav>
  );
}
