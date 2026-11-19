import { expect, test } from "@playwright/test";
import { readJson, writeJson } from "./recruitment-helpers";

test("retrying after a lost ticket response opens the original ticket", async ({ page }) => {
  await writeJson(page.request, "/auth/login", {
    email: "director@example.com", password: process.env.SEED_PASSWORD!,
  });
  await page.goto("/tickets/new");
  await page.getByLabel("Requester name").fill("Sample Lost Response");
  await page.getByLabel("Requester email").fill("lost-response@example.org");
  await page.getByLabel("Subject").fill("Sample ticket retry");
  await page.getByLabel("Message body").fill("This is a fabricated requester message.");
  const submit = page.getByRole("button", { name: "Create simulated ticket" });
  await page.route(/\/api\/tickets$/, async route => {
    const response = await route.fetch();
    expect(response.ok()).toBeTruthy();
    await route.abort("failed");
  }, { times: 1 });
  await submit.click();
  await expect(page.getByRole("alert")).toBeVisible();
  await expect(page).toHaveURL(/\/tickets\/new$/);
  const list = () => readJson(page.request, "/tickets?q=Sample%20ticket%20retry");
  const first = await list();
  expect(first.total).toBe(1);
  await submit.click();
  await expect(page).toHaveURL(new RegExp(`/tickets/${first.items[0].id}$`));
  expect((await list()).total).toBe(1);
});

test("campaign transitions are disabled while approval submission is pending", async ({ page }) => {
  await writeJson(page.request, "/auth/login", {
    email: "director@example.com", password: process.env.SEED_PASSWORD!,
  });
  const campaign = await writeJson(page.request, "/campaigns", {
    name: "Sample pending submission",
    subject_template: "Sample subject",
    body_template: "Fabricated sandbox message.",
    recipients: [{ email: "pending-submit@example.org" }],
  });
  await page.goto(`/campaigns/${campaign.id}`);
  let resume!: () => void;
  const heldSubmission = new Promise<void>(resolve => { resume = resolve; });
  await page.route(`**/api/campaigns/${campaign.id}/submit`, async route => {
    await heldSubmission;
    await route.continue();
  });
  const submit = page.getByRole("button", { name: "Submit for approval" });
  await submit.click();
  try {
    await expect(submit).toBeDisabled();
    await expect(page.getByRole("button", { name: "Cancel campaign" })).toBeDisabled();
  } finally {
    resume();
  }
  await expect(page.getByRole("button", { name: "Approve campaign" })).toBeVisible();
});
