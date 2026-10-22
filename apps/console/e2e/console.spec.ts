import { execFileSync } from "node:child_process";

import { expect, test, type Page } from "@playwright/test";

// G6b (§13): sign-in, create workload, create key, calibrate, activate, enforce toggle disabled
// until §4 C9's conditions hold, override. Everything runs against the real local stack.

const repo = new URL("../../..", import.meta.url).pathname;
const MAILPIT = process.env.MAILPIT_URL ?? "http://127.0.0.1:54324";
const WORKLOAD = "e2e.triage";

function seed(...args: string[]): void {
  execFileSync("uv", ["run", "--frozen", "python", "apps/console/e2e/seed.py", ...args], {
    cwd: repo,
    stdio: "inherit",
    timeout: 180_000,
  });
}

interface MailpitList {
  messages: { ID: string }[];
}

async function magicLink(email: string): Promise<string> {
  const deadline = Date.now() + 30_000;
  while (Date.now() < deadline) {
    const search = await fetch(
      `${MAILPIT}/api/v1/search?query=${encodeURIComponent(`to:"${email}"`)}`,
    );
    const found = (await search.json()) as MailpitList;
    const first = found.messages[0];
    if (first) {
      const message = (await (await fetch(`${MAILPIT}/api/v1/message/${first.ID}`)).json()) as {
        Text: string;
      };
      const link = /https?:\/\/\S+\/auth\/v1\/verify\?\S+/.exec(message.Text)?.[0];
      if (link) return link.replace(/[)\]>]+$/, "");
    }
    await new Promise((r) => setTimeout(r, 500));
  }
  throw new Error(`no sign-in email for ${email}`);
}

/** Reload until `check` passes: the worker runs jobs asynchronously. */
async function eventually(page: Page, check: () => Promise<void>, timeout = 90_000): Promise<void> {
  await expect(async () => {
    await page.reload();
    await check();
  }).toPass({ timeout, intervals: [1_000, 2_000] });
}

test("an admin takes a workload from first data to enforce, then overrides it", async ({ page }) => {
  page.on("dialog", (dialog) => void dialog.accept());
  const email = `e2e-${String(Date.now())}@example.test`;

  await test.step("sign in with a magic link", async () => {
    await page.goto("/o/00000000-0000-0000-0000-000000000000");
    await expect(page).toHaveURL(/\/login$/); // signed-out users are sent to sign in
    await page.getByLabel("Email").fill(email);
    await page.getByRole("button", { name: "Email me a link" }).click();
    await expect(page.getByRole("status")).toContainText("Check");
    await page.goto(await magicLink(email));
    await expect(page).toHaveURL(/\/orgs$/);
    await expect(page.getByText(email)).toBeVisible();
  });

  await test.step("create an organisation and a project", async () => {
    await expect(page.getByRole("heading", { name: "Organisations" })).toBeVisible();
    await page.getByLabel("Name", { exact: true }).fill("E2E Org");
    await page.getByRole("button", { name: "Create organisation" }).click();
    await expect(page).toHaveURL(/\/o\/[0-9a-f-]{36}$/);
    await page.getByLabel("Project name").fill("agents");
    await page.getByRole("button", { name: "Create project" }).click();
    await expect(page.getByRole("cell", { name: "agents", exact: true })).toBeVisible();
  });
  const org = /\/o\/([0-9a-f-]{36})/.exec(page.url())?.[1] ?? "";

  let key = "";
  await test.step("create an API key, shown once", async () => {
    await page.getByRole("link", { name: "API keys" }).click();
    await expect(page.getByRole("heading", { name: "API keys", level: 1 })).toBeVisible();
    await page.getByLabel("Name", { exact: true }).fill("e2e agents");
    await page.getByRole("button", { name: "Create key" }).click();
    const secret = page.getByLabel("secret");
    await expect(secret).toHaveText(/^sc_/);
    key = (await secret.textContent()) ?? "";
    await page.reload();
    await expect(page.getByLabel("secret")).toHaveCount(0); // never shown again
    await expect(page.getByRole("cell", { name: "e2e agents" })).toBeVisible();
  });

  await test.step("create a workload", async () => {
    await page.getByRole("link", { name: "Workloads" }).click();
    await expect(page.getByRole("heading", { name: "Workloads", level: 1 })).toBeVisible();
    await page.getByLabel("Name", { exact: true }).fill(WORKLOAD);
    await page.getByRole("button", { name: "Create workload" }).click();
    await page.getByRole("link", { name: WORKLOAD }).click();
    await expect(page.getByTestId("mode")).toHaveText("shadow");
  });

  await test.step("enforce is disabled before there is a bundle or a shadow week", async () => {
    await expect(page.getByRole("button", { name: "Switch to enforce" })).toBeDisabled();
    await expect(page.getByTestId("enforce-blocked")).toHaveCount(2);
  });

  await test.step("calibrate on eight days of shadow data", async () => {
    seed("history", "--key", key, "--workload", WORKLOAD);
    await page.getByRole("button", { name: "Calibrate" }).click();
    await expect(page.getByRole("status").first()).toContainText("Calibration queued");
    await eventually(page, async () => {
      await expect(page.getByTestId("bundle-v1")).toContainText("validated");
    });
    // A validated bundle alone is not enough: there is no 7-day shadow report yet.
    await expect(page.getByRole("button", { name: "Switch to enforce" })).toBeDisabled();
    await expect(page.getByTestId("enforce-blocked")).toHaveText([/shadow report covering 7 days/]);
  });

  await test.step("activate the validated bundle", async () => {
    await page.getByTestId("bundle-v1").getByRole("button", { name: "Activate" }).click();
    await expect(page.getByTestId("bundle-v1")).toContainText("active");
  });

  await test.step("a shadow week of reports opens the enforce toggle", async () => {
    seed("report", "--org", org, "--workload", WORKLOAD);
    await page.reload();
    await expect(page.getByTestId("enforce-blocked")).toHaveCount(0);
    await expect(page.getByRole("button", { name: "Switch to enforce" })).toBeEnabled();
    await expect(page.getByRole("img", { name: /Savings per day/ })).toBeVisible();
    await expect(page.getByTestId("outcome-pending")).toHaveText("0"); // every seeded run has one
    await page.getByRole("link", { name: "Workloads" }).first().click();
    const row = page.getByRole("row", { name: new RegExp(WORKLOAD.replace(".", "\\.")) });
    await expect(row).toContainText("v1");
    await expect(row).toContainText("$");
    await row.getByRole("link", { name: WORKLOAD }).click();
  });

  await test.step("the run explorer shows score against threshold", async () => {
    seed("sdk-runs", "--key", key, "--workload", WORKLOAD);
    await page.getByRole("link", { name: "Explore runs →" }).click();
    await page.getByRole("row").nth(1).getByRole("link").click();
    await expect(page.getByRole("img", { name: "Score against threshold per step" })).toBeVisible();
    await expect(page.getByTestId("threshold")).toBeAttached();
    await page.goBack();
    await page.goBack();
  });

  await test.step("switch to enforce: the worker signs a new version", async () => {
    await page.getByRole("button", { name: "Switch to enforce" }).click();
    await expect(page.getByTestId("mode")).toHaveText("enforce");
    await eventually(page, async () => {
      await expect(page.getByTestId("bundle-v2")).toContainText("active");
    });
  });

  await test.step("emergency override back to shadow, recorded in the audit log", async () => {
    await page.getByRole("button", { name: "Override to shadow" }).click();
    await expect(page.getByTestId("override")).toBeVisible();
    await page.getByRole("link", { name: "Audit log" }).click();
    await expect(page.getByRole("cell", { name: "workload.override" })).toBeVisible();
    await expect(page.getByRole("cell", { name: "bundle.activate" })).toBeVisible();
  });

  await test.step("revoke the key", async () => {
    await page.getByRole("link", { name: "API keys" }).click();
    await page.getByRole("button", { name: "Revoke" }).click();
    await expect(page.getByText(/^revoked/)).toBeVisible();
  });
});
