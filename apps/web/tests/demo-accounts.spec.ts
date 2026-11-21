import { expect, test } from "@playwright/test";

test("the E2E preview includes the Lead and Group Projects Head logins", async ({ page }) => {
  async function signIn(email: string, password: string) {
    await page.goto("/login");
    await page.getByLabel("Email", { exact: true }).fill(email);
    await page.getByLabel("Password", { exact: true }).fill(password);
    await page.getByRole("button", { name: "Sign in", exact: true }).click();
    await expect(page.getByRole("heading", { name: "Operations overview" })).toBeVisible();
    const response = await page.request.get("/api/auth/me");
    expect(response.ok()).toBe(true);
    return response.json();
  }

  expect(await signIn("lead@example.com", "lead")).toMatchObject({
    email: "lead@example.com", role: "competitions_chair",
  });
  await page.getByRole("button", { name: "Sign out" }).click();
  await expect(page.getByRole("button", { name: "Sign in", exact: true })).toBeVisible();

  expect(await signIn("gch@example.com", "gch")).toMatchObject({
    email: "gch@example.com", role: "competitions_member", department: { slug: "group-projects" },
  });
  await expect(page.getByRole("heading", { name: "Group Projects", exact: true })).toBeVisible();
  await expect(page.getByRole("link", { name: "Open Group Projects competitions" })).toBeVisible();
  await expect(page.getByRole("heading", { name: "Competitions activity", exact: true })).toBeVisible();
});
