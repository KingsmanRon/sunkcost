import { redirect } from "next/navigation";

import { myOrgs } from "@/lib/data";

export default async function Home() {
  const orgs = await myOrgs();
  redirect(orgs[0] ? `/o/${orgs[0].id}` : "/orgs");
}
