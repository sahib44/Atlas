import { expect, test } from "@playwright/test";

test("signed-out session checks leave the sign-in form and its inputs in place", async ({ page }) => {
  await page.clock.install();
  let sessionChecks = 0;
  await page.route("**/api/auth/me", async route => {
    sessionChecks += 1;
    // Model a slow session check so a loading-state flash would unmount the form.
    if (sessionChecks > 1) await new Promise(resolve => setTimeout(resolve, 500));
    await route.continue();
  });
  await page.goto("/login");
  const email = page.getByLabel("Email", { exact: true });
  const password = page.getByLabel("Password", { exact: true });
  await email.fill("chair@example.com");
  await password.fill("typing-in-progress");
  await page.clock.runFor(6200);
  await expect(email).toHaveValue("chair@example.com");
  await expect(password).toHaveValue("typing-in-progress");
  await expect(password).toBeFocused();
  expect(sessionChecks).toBe(1);

  await password.fill(process.env.SEED_PASSWORD!);
  await page.getByRole("button", { name: "Sign in", exact: true }).click();
  await expect(page).toHaveURL(/\/dashboard$/);
  await expect(page.getByRole("heading", { name: "Operations overview" })).toBeVisible();
});
