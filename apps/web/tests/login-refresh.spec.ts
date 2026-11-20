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

test("sign-in reports field errors and lets users reveal their password", async ({ page }) => {
  let attempts = 0;
  let rejectLogin!: () => void;
  const responseReady = new Promise<void>(resolve => { rejectLogin = resolve; });
  await page.route("**/api/auth/login", async route => {
    attempts++;
    await responseReady;
    await route.fulfill({
      status: 401,
      json: { detail: { code: "invalid_credentials", message: "Email or password is incorrect." } },
    });
  });

  await page.goto("/login");
  const email = page.getByLabel("Email", { exact: true });
  const password = page.getByLabel("Password", { exact: true });
  const signIn = page.getByRole("button", { name: "Sign in", exact: true });
  await email.fill("invalid-email");
  await signIn.click();
  await expect(email).toHaveAccessibleDescription("Enter a valid email address.");
  await expect(password).toHaveAccessibleDescription("Enter your password.");
  await expect(email).toBeFocused();
  expect(attempts).toBe(0);

  await email.fill("chair@example.com");
  await password.fill("fabricated-password");
  await page.getByRole("button", { name: "Show password" }).press("Enter");
  await expect(password).toHaveAttribute("type", "text");
  await page.getByRole("button", { name: "Hide password" }).press("Enter");
  await expect(password).toHaveAttribute("type", "password");
  await expect(password).toHaveValue("fabricated-password");
  expect(attempts).toBe(0);

  await signIn.click();
  try {
    await expect(page.getByRole("button", { name: "Signing in…" })).toBeDisabled();
  } finally {
    rejectLogin();
  }
  await expect(page.getByRole("alert")).toHaveText("Email or password is incorrect.");
  await expect(signIn).toBeEnabled();
  expect(attempts).toBe(1);
});
