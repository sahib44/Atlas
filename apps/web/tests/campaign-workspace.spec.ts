import { expect, test } from "@playwright/test";
import { writeJson } from "./recruitment-helpers";

test("campaigns and judge conversations keep independent searches and pages", async ({ page }) => {
  const judgeId = "2c121b6a-f8cf-4e78-a1a8-6a34c8cab390";
  const campaignId = "f7014b1a-04e7-4c84-82a8-e5744be9723e";
  const date = "2027-01-22T12:00:00Z";
  await writeJson(page.request, "/auth/login", {
    email: "competitions@example.com", password: process.env.SEED_PASSWORD!,
  });

  // Keep the pagination fixture independent of other sandbox workflows.
  await page.route(/\/api\/campaigns(?:\?|$)/, async route => {
    const params = new URL(route.request().url()).searchParams;
    const pageNumber = Number(params.get("page") || 1);
    const pageSize = Number(params.get("page_size") || 25);
    const filtered = params.get("q") === "winter" && params.get("status") === "draft";
    await route.fulfill({ json: {
      items: [{ id: campaignId, name: `${filtered ? "Winter" : "Atlas"} campaign ${pageNumber}`, subject_template: "Judge invitations", status: "draft", created_at: date, updated_at: date }],
      total: 51, page: pageNumber, page_size: pageSize,
    } });
  });
  await page.route(/\/api\/conversations(?:\?|$)/, async route => {
    const params = new URL(route.request().url()).searchParams;
    const pageNumber = Number(params.get("page") || 1);
    const matched = params.get("judge_id") === judgeId;
    const name = matched ? "Linked judge conversation" : `${params.get("q") === "avery" ? "Avery" : "Sample"} conversation ${pageNumber}`;
    await route.fulfill({ json: {
      items: [{ id: judgeId, name, email: "avery@example.org", unread_count: 0, last_message_at: date }],
      total: matched ? 1 : 51, page: pageNumber, page_size: 25,
    } });
  });

  await page.goto("/campaigns");
  await expect(page.getByRole("heading", { name: "Email Outreach" })).toBeVisible();
  const campaignsTab = page.getByRole("link", { name: "Campaigns", exact: true });
  const conversationsTab = page.getByRole("link", { name: "Judge Conversations", exact: true });
  const campaignSearch = page.getByLabel("Search outreach campaigns", { exact: true });
  const campaignStatus = page.getByLabel("Campaign status", { exact: true });
  const conversationSearch = page.getByLabel("Search judge conversations", { exact: true });
  const simulate = page.getByRole("button", { name: "Simulate incoming email" });
  const nextPage = page.getByRole("button", { name: "Next page" });
  await expect(simulate).toHaveCount(0);
  await campaignSearch.fill("winter");
  await campaignStatus.selectOption("draft");
  await expect(page.getByText("Winter campaign 1", { exact: true })).toBeVisible();
  await nextPage.click();
  await expect(page.getByText("Winter campaign 2", { exact: true })).toBeVisible();

  await conversationsTab.click();
  await expect(simulate).toBeVisible();
  await expect(campaignStatus).toHaveCount(0);
  await expect(conversationSearch).toHaveValue("");
  await conversationSearch.fill("avery");
  await expect(page.getByText("Avery conversation 1", { exact: true })).toBeVisible();
  await nextPage.click();
  await expect(page.getByText("Avery conversation 2", { exact: true })).toBeVisible();

  await campaignsTab.click();
  await expect(campaignSearch).toHaveValue("winter");
  await expect(campaignStatus).toHaveValue("draft");
  await expect(page.getByText("Winter campaign 2", { exact: true })).toBeVisible();
  await conversationsTab.click();
  await expect(conversationSearch).toHaveValue("avery");
  await expect(page.getByText("Avery conversation 2", { exact: true })).toBeVisible();

  await page.goto(`/campaigns?judge=${judgeId}`);
  await expect(page.getByText("Linked judge conversation", { exact: true })).toBeVisible();
  await expect(page.getByRole("link", { name: "Show all conversations" })).toBeVisible();
  await expect(campaignSearch).toHaveCount(0);

  await page.getByRole("link", { name: "Show all conversations" }).click();
  await expect(page.getByText("Sample conversation 1", { exact: true })).toBeVisible();
  await expect(page).toHaveURL(/view=conversations$/);
  await page.goto(`/campaigns?judge=${judgeId}`);

  for (const width of [1024, 390]) {
    await page.setViewportSize({ width, height: 900 });
    await campaignsTab.click();
    await expect(campaignSearch).toBeVisible();
    await expect(page.getByText("Atlas campaign 1", { exact: true })).toBeVisible();
    await expect.poll(() => page.evaluate(() => document.documentElement.scrollWidth <= window.innerWidth)).toBe(true);
    await conversationsTab.click();
    await expect(conversationSearch).toBeVisible();
    await expect(page.getByText("Linked judge conversation", { exact: true })).toBeVisible();
    await expect.poll(() => page.evaluate(() => document.documentElement.scrollWidth <= window.innerWidth)).toBe(true);
  }
});
