import { expect, test } from "@playwright/test";

test("profile saves per account and returns to the previous workspace", async ({ page }) => {
  const password = process.env.SEED_PASSWORD;
  if (!password) throw new Error("SEED_PASSWORD is required");
  async function signIn(email: string) {
    await page.goto("/login");
    await page.getByLabel("Email", { exact: true }).fill(email);
    await page.getByLabel("Password", { exact: true }).fill(password!);
    await page.getByRole("button", { name: "Sign in", exact: true }).click();
    await expect(page.getByRole("heading", { name: "Operations overview" })).toBeVisible();
  }

  await signIn("competitions@example.com");
  await expect(page.locator(".topbar").getByRole("searchbox")).toHaveCount(0);
  await page.goto("/judges?event_year=2027&q=visual");
  const directoryUrl = page.url();
  await page.getByRole("link", { name: "Open profile" }).click();
  await expect(page.getByRole("heading", { name: "Profile" })).toBeVisible();
  await page.getByRole("button", { name: "Back", exact: true }).click();
  await expect(page).toHaveURL(directoryUrl);

  await page.getByRole("link", { name: "Open profile" }).click();
  await page.getByLabel("Display name").fill("Discard this name");
  await page.getByRole("button", { name: "Back", exact: true }).click();
  const prompt = page.getByRole("dialog", { name: "Save profile changes?" });
  await prompt.getByRole("button", { name: "Discard changes" }).click();
  await expect(page).toHaveURL(directoryUrl);

  await page.getByRole("link", { name: "Open profile" }).click();
  await expect(page.getByLabel("Display name")).toHaveValue("Competition Heads");
  await page.getByLabel("Display name").fill("Group Projects Head");
  await page.getByRole("button", { name: "Save", exact: true }).click();
  await expect(page.getByRole("status")).toContainText("Profile saved.");
  await page.getByLabel("Display name").fill("Group Projects Organizer");
  await page.getByRole("button", { name: "Back", exact: true }).click();
  await prompt.getByRole("button", { name: "Save and go back" }).click();
  await expect(page).toHaveURL(directoryUrl);
  await expect(page.locator(".workspace-copy")).toContainText("Group Projects Organizer");

  await page.getByRole("button", { name: "Sign out" }).click();
  await expect(page.getByRole("button", { name: "Sign in", exact: true })).toBeVisible();
  await signIn("chair@example.com");
  await page.getByRole("link", { name: "Open profile" }).click();
  await expect(page.getByLabel("Display name")).toHaveValue("Competitions Chair");
});
