import { expect, test } from "@playwright/test";
import { addApprovedNomination, approveNomination } from "./recruitment-helpers";

test("chair confirms and moves official judges while browsing tournament coverage", async ({ page }) => {
  const password = process.env.SEED_PASSWORD;
  if (!password) throw new Error("SEED_PASSWORD is required");
  await page.goto("/login");
  await page.getByLabel("Email", { exact: true }).fill("chair@example.com");
  await page.getByLabel("Password", { exact: true }).fill(password);
  await page.getByRole("button", { name: "Sign in", exact: true }).click();
  await expect(page.getByRole("heading", { name: "Operations overview" })).toBeVisible();

  const read = async (path: string) => {
    const response = await page.request.get(`/api${path}`);
    expect(response.ok()).toBe(true);
    return response.json();
  };
  const requestHeaders = { "X-MIST-Request": "1" };
  const archive = await page.request.post("/api/competitions", {
    headers: requestHeaders,
    multipart: { name: "Prior Year Showcase", event_year: "2026", category_slug: "arts" },
  });
  expect(archive.ok()).toBe(true);
  const editions = await read("/competitions?category_slug=arts&event_year=2027");
  const first = editions.items.find((row: { name: string }) => row.name === "2D Art");
  const second = editions.items.find((row: { name: string }) => row.name === "3D Art");

  await page.getByRole("link", { name: "Competitions", exact: true }).first().click();
  const tournament = page.getByRole("combobox", { name: "Tournament", exact: true });
  await expect(tournament).toHaveValue("2027");
  await tournament.selectOption("2026");
  await expect(page.getByRole("progressbar", { name: "Arts confirmed judge coverage" })).toHaveJSProperty("max", 3);
  await page.locator(".pilot-card").filter({ hasText: "Arts" }).click();
  await expect(page.locator(".pilot-card").filter({ hasText: "Prior Year Showcase" })).toBeVisible();
  await expect(page.locator(".pilot-card").filter({ hasText: "2D Art" })).toHaveCount(0);
  await tournament.selectOption("2027");
  await page.locator(".pilot-card").filter({ hasText: "2D Art" }).click();

  const roster = page.locator(".official-roster");
  await expect(roster.getByText("Vacant", { exact: true })).toHaveCount(3);
  await addApprovedNomination(page, first.id, "Official Sample", "official-sample@example.org");
  const nomination = page.locator(".pilot-judge").filter({ hasText: "Official Sample" });
  await nomination.getByLabel("Response", { exact: true }).selectOption("accepted");
  await expect(roster.getByRole("progressbar")).toHaveJSProperty("value", 0);
  await nomination.getByRole("button", { name: "Confirm assignment", exact: true }).click();
  const confirm = page.getByRole("dialog", { name: "Confirm official judge" });
  await confirm.getByLabel("Official slot").selectOption("2");
  await confirm.getByRole("button", { name: "Confirm assignment", exact: true }).click();
  await expect(roster.getByRole("progressbar")).toHaveJSProperty("value", 1);
  await expect(roster.getByRole("link", { name: "Official Sample" })).toBeVisible();
  await page.screenshot({ path: "test-results/phase2-official-slots-light.png", fullPage: true });

  const judges = await read(`/competitions/${first.id}/judges`);
  const judgeId = judges.items.find((row: { judge: { display_name: string } }) => row.judge.display_name === "Official Sample").judge_id;
  const attached = await page.request.post(`/api/competitions/${second.id}/judges`, {
    headers: requestHeaders,
    data: { name: "Official Sample", existing_judge_id: judgeId, qualifications: "Experienced reviewer." },
  });
  expect(attached.ok()).toBe(true);
  await approveNomination(page.request, await attached.json());
  await page.goto(`/competitions/arts/${second.id}`);
  const destination = page.locator(".pilot-judge").filter({ hasText: "Official Sample" });
  await destination.getByLabel("Response", { exact: true }).selectOption("accepted");
  await expect(destination).toContainText("Official slot in 2D Art");
  await destination.getByRole("button", { name: "Reassign judge", exact: true }).click();
  const move = page.getByRole("dialog", { name: "Reassign judge" });
  await expect(move).toContainText("releases slot 2 in 2D Art");
  await move.getByRole("button", { name: "Reassign judge", exact: true }).click();
  await expect(roster.getByRole("progressbar")).toHaveJSProperty("value", 1);
  await expect.poll(async () => (await read(`/competitions/${first.id}`)).confirmed_count).toBe(0);
  await page.getByRole("button", { name: "Switch to dark mode" }).click();
  await page.screenshot({ path: "test-results/phase2-official-slots-dark.png", fullPage: true });

  await roster.getByRole("button", { name: "Release slot 1" }).click();
  await page.getByRole("dialog", { name: "Release official slot" }).getByRole("button", { name: "Release assignment" }).click();
  await expect(roster.getByRole("progressbar")).toHaveJSProperty("value", 0);
  await expect(destination.getByLabel("Response", { exact: true })).toHaveValue("accepted");
  await roster.getByRole("button", { name: "Assignment history" }).click();
  await expect(page.getByRole("dialog", { name: "Assignment history" })).toContainText("Official Sample");
  await expect(page.getByRole("dialog", { name: "Assignment history" })).toContainText("Released");
  await page.getByRole("button", { name: "Close dialog" }).click();

  await page.setViewportSize({ width: 390, height: 844 });
  expect(await page.evaluate(() => document.documentElement.scrollWidth <= window.innerWidth)).toBe(true);
  await page.goto("/competitions?event_year=2026");
  await expect(tournament).toHaveValue("2026");
  await expect(page.getByRole("progressbar", { name: "Arts confirmed judge coverage" })).toHaveJSProperty("value", 0);
});
