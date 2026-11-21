import { expect, test, type Page } from "@playwright/test";

const positions = [
  ["arts", "Arts", "arts"],
  ["knowledge-quran", "Knowledge & Quran", "knowledge-quran"],
  ["writing-oratory", "Writing & Oratory", "writing-oratory"],
  ["brackets", "Brackets", "brackets"],
  ["group-projects", "Group Projects", "group-projects"],
  ["brothers-sports", "Brothers Sports", "sports-referees"],
  ["sisters-sports", "Sisters Sports", "sports-referees"],
].map(([slug, name, category_slug]) => ({
  slug, name, category_slug, state: "unassigned", email: null as string | null,
  invitation_status: "none", assignment_id: null as string | null, account_id: null,
}));

const ledBy = {
  id: "visual-fixture-lead",
  full_name: "Fixture Lead",
  email: "fixture.lead@getmistified.com",
  role: "competitions_chair",
  managed_account: true,
  is_active: true,
  permissions: [],
  department: null,
};

const worksIn = {
  ...ledBy,
  id: "visual-fixture-head",
  full_name: "Fixture Head",
  email: "fixture.head@getmistified.com",
  role: "competitions_member",
  department: { slug: "group-projects", name: "Group Projects", category_slug: "group-projects" },
};

const arts = {
  id: "fixture-arts", name: "Photography", category_slug: "arts", event_year: 2027,
  is_pilot: false, confirmed_count: 0, required_count: 3, unread_count: 0,
};
const group = {
  ...arts, id: "fixture-group-projects", name: "Science Fair", category_slug: "group-projects",
};
const accountUrl = "/api/accounts";
const invitationEmail = "fixture.arts@getmistified.com";
const emptyPage = { items: [], total: 0, page: 1, page_size: 25 };

async function stubWorkspace(page: Page, user: typeof ledBy | typeof worksIn | null) {
  const departmentQueries: string[] = [];
  const invitations: Record<string, unknown>[] = [];
  const directory = positions.map(position => ({ ...position }));
  await page.route("**/api/**", async route => {
    const request = route.request();
    const url = new URL(request.url());
    switch (url.pathname) {
      case "/api/auth/account-mode":
        return route.fulfill({ json: { managed: true } });
      case "/api/auth/me":
        if (user) return route.fulfill({ json: user });
        return route.fulfill({ status: 401, json: { detail: { code: "unauthenticated", message: "Please sign in." } } });
      case "/api/dashboard":
        return route.fulfill({ json: { metrics: [], groups: {}, recent_activity: [], latest_import: null } });
      case "/api/notifications":
        return route.fulfill({ json: { items: [], unread_count: 0 } });
      case "/api/competitions/tournaments":
        return route.fulfill({ json: { years: [2027, 2026], default_year: 2027 } });
      case "/api/competitions/categories":
        return route.fulfill({ json: [
          { slug: "arts", name: "Arts", competition_count: 1, confirmed_count: 0, required_count: 3, unread_count: 0 },
          { slug: "group-projects", name: "Group Projects", competition_count: 1, confirmed_count: 0, required_count: 3, unread_count: 0 },
        ] });
      case "/api/competitions": {
        const category = url.searchParams.get("category_slug");
        const items = [arts, group].filter(row => !category || row.category_slug === category);
        return route.fulfill({ json: { ...emptyPage, items, total: items.length } });
      }
      case `${accountUrl}/department-dashboard`:
        departmentQueries.push(url.search);
        return route.fulfill({ json: { items: [group], activity: [], total: 1 } });
      case `${accountUrl}/positions`:
        return route.fulfill({ json: { items: directory } });
      case `${accountUrl}/positions/arts/invite`: {
        invitations.push(request.postDataJSON());
        directory[0] = {
          ...directory[0], state: "invited", email: invitationEmail, invitation_status: "pending",
          assignment_id: "71fdc37a-5789-487e-be6f-4e651edc61bc",
        };
        return route.fulfill({ json: directory[0] });
      }
      case `${accountUrl}/positions/arts/history`:
        return route.fulfill({ json: { items: [{
          id: "fixture-invitation-event", action: "account.invitation_created",
          summary: "Invited fixture.arts@getmistified.com", created_at: "2027-01-22T12:00:00Z",
        }] } });
      default:
        return route.fulfill({ json: emptyPage });
    }
  });
  return { departmentQueries, invitations };
}

test("Lead sees the shared dashboard and can invite a head into an open position", async ({ page }) => {
  const { departmentQueries, invitations } = await stubWorkspace(page, ledBy);
  await page.goto("/dashboard");
  await expect(page.getByRole("heading", { name: "Operations overview" })).toBeVisible();
  await expect(page.getByRole("link", { name: "Open all competitions" })).toHaveCount(0);
  expect(departmentQueries).toHaveLength(0);

  await page.getByRole("link", { name: "User Management", exact: true }).click();
  await expect(page.getByRole("heading", { name: "User Management", exact: true })).toBeVisible();
  for (const { name } of positions) {
    await expect(page.getByRole("region", { name, exact: true })).toBeVisible();
  }
  const artsPosition = page.getByRole("region", { name: "Arts", exact: true });
  await artsPosition.getByRole("button", { name: "Invite", exact: true }).click();
  const dialog = page.getByRole("dialog", { name: "Invite to Arts" });
  await dialog.getByLabel("MIST email address").fill(invitationEmail);
  await dialog.getByRole("button", { name: "Send invitation" }).click();
  await expect(artsPosition.getByText(invitationEmail, { exact: true })).toBeVisible();
  expect(invitations).toEqual([{ email: invitationEmail }]);
  await expect(artsPosition.getByRole("button", { name: "Resend invitation", exact: true })).toBeVisible();
  await artsPosition.getByRole("button", { name: "History", exact: true }).click();
  await expect(page.getByRole("dialog").getByText(`Invited ${invitationEmail}`, { exact: true })).toBeVisible();
});

test("a head sees their primary competitions and can browse other competitions", async ({ page }) => {
  const { departmentQueries } = await stubWorkspace(page, worksIn);
  await page.goto("/dashboard");
  await expect(page.getByRole("heading", { name: "Operations overview" })).toBeVisible();
  await expect(page.getByRole("heading", { name: "Group Projects", exact: true })).toBeVisible();
  await expect(page.getByText("Your primary competitions · 3 judge slots open")).toBeVisible();
  await expect(page.getByRole("heading", { name: "Competitions activity", exact: true })).toBeVisible();
  await expect(page.getByText("No recent competition activity.")).toBeVisible();
  await expect(page.getByText("Science Fair", { exact: true }).first()).toBeVisible();
  expect(departmentQueries).toContain("?event_year=2027");
  await expect(page.getByRole("link", { name: "User Management", exact: true })).toHaveCount(0);
  await expect(page.getByRole("combobox", { name: /department/i })).toHaveCount(0);

  await expect(page.getByRole("link", { name: "Open Group Projects competitions" }))
    .toHaveAttribute("href", "/competitions/group-projects?event_year=2027");
  await page.getByRole("link", { name: "Open all competitions" }).click();
  await page.getByRole("link", { name: /^Arts / }).click();
  await expect(page.getByRole("heading", { name: "Arts", exact: true })).toBeVisible();
  await expect(page.getByRole("link", { name: /^Photography / })).toBeVisible();
  await page.getByRole("link", { name: "Overview", exact: true }).click();
  await expect(page.getByRole("heading", { name: "Group Projects", exact: true })).toBeVisible();

  await page.goto("/user-management");
  await expect(page.getByRole("heading", { name: "Access restricted", exact: true })).toBeVisible();
});

test("activation requires verified recovery before opening the workspace", async ({ page }) => {
  await stubWorkspace(page, null);
  const password = "fixture-activation-password";
  const invitationToken = "i".repeat(64);
  const methodToken = "m".repeat(64);
  const backup = "fixture.person@example.com";
  const mask = "f•••@example.com";
  let signedIn = false;
  let recoveryRequired = true;
  let added = false;
  let activation: unknown;
  let verification: unknown;

  const profile = () => ({ ...worksIn, recovery_required: recoveryRequired });
  await page.route("**/api/auth/me", route => signedIn ? route.fulfill({ json: profile() }) : route.fulfill({ status: 401 }));
  await page.route("**/api/auth/login", route => {
    expect(route.request().postDataJSON()).toEqual({ email: worksIn.email, password });
    signedIn = true;
    return route.fulfill({ json: profile() });
  });
  await page.route(`**${accountUrl}/recovery`, route => route.fulfill({ json: {
    methods: added ? [{ id: "fixture-method", kind: "email", masked_destination: mask, verified: !recoveryRequired, preferred: !recoveryRequired }] : [],
    allow_mist_email: false,
    sms_available: false,
  } }));
  await page.route(`**${accountUrl}/recovery/methods`, route => {
    expect(route.request().postDataJSON()).toEqual({ kind: "email", destination: backup, password });
    added = true;
    return route.fulfill({ json: { ok: true } });
  });
  await page.route(`**${accountUrl}/recovery/verify`, route => {
    verification = route.request().postDataJSON();
    recoveryRequired = false;
    return route.fulfill({ json: { ok: true } });
  });
  await page.route(`**${accountUrl}/activate`, route => {
    activation = route.request().postDataJSON();
    return route.fulfill({ json: { ok: true, email: worksIn.email } });
  });

  await page.goto(`/activate#token=${invitationToken}`);
  await expect(page.getByRole("heading", { name: "Activate your account", exact: true })).toBeVisible();
  await expect(page).toHaveURL(/\/activate$/);
  await expect(page.getByLabel("MIST email address")).toHaveCount(0);
  await page.getByLabel("Full name").fill("Fixture Arts Head");
  await page.getByLabel("New password", { exact: true }).fill(password);
  await page.getByLabel("Confirm password", { exact: true }).fill(password);
  await page.getByRole("button", { name: "Activate account" }).click();
  await expect.poll(() => activation).toEqual({
    token: invitationToken, full_name: "Fixture Arts Head", password,
  });
  await expect(page).toHaveURL(/\/recovery-setup$/);
  await expect(page.getByRole("heading", { name: "Set up account recovery" })).toBeVisible();
  await page.goto("/campaigns");
  await expect(page).toHaveURL(/\/recovery-setup$/);
  await expect(page.getByRole("heading", { name: "Email Outreach" })).toHaveCount(0);
  await expect(page.getByText("Use a personal email outside @getmistified.com.")).toBeVisible();
  const sms = page.getByRole("option", { name: "Phone (SMS)" });
  await expect(sms).toBeDisabled();

  await page.setViewportSize({ width: 390, height: 844 });
  for (const [label, theme] of [["Light", "light"], ["Dark", "dark"], ["Purple Dark", "purple-dark"]]) {
    await page.getByRole("button", { name: "Appearance" }).click();
    await page.getByRole("button", { name: label, exact: true }).click();
    await expect(page.locator("html")).toHaveAttribute("data-theme", theme);
    const hasHorizontalOverflow = await page.evaluate(() => document.documentElement.scrollWidth > innerWidth);
    expect(hasHorizontalOverflow).toBe(false);
  }

  await page.getByLabel("Recovery email address").fill(backup);
  await page.getByLabel("Current Atlas password").fill(password);
  await page.getByRole("button", { name: "Send verification" }).click();
  await expect(page.getByText(mask)).toBeVisible();
  await expect(page.getByText("Unverified", { exact: true })).toBeVisible();
  await expect(page.getByLabel("Recovery email address")).toHaveCount(0);
  expect(await page.locator("body").innerText()).not.toContain(backup);

  signedIn = false;
  await page.goto(`/verify-recovery#token=${methodToken}`);
  await expect(page.getByText("Sign in to verify your recovery method.")).toBeVisible();
  await expect(page).toHaveURL(/\/verify-recovery$/);
  await page.getByLabel("Email", { exact: true }).fill(worksIn.email);
  await page.getByLabel("Password", { exact: true }).fill(password);
  await page.getByRole("button", { name: "Sign in", exact: true }).click();
  await page.getByRole("button", { name: "Verify recovery method" }).click();
  expect(verification).toEqual({ token: methodToken });
  await expect(page.getByRole("heading", { name: "Operations overview" })).toBeVisible();
});

test("password recovery sends only to the supplied backup and gives a generic confirmation", async ({ page }) => {
  await stubWorkspace(page, null);
  const email = "fixture.arts@getmistified.com";
  const backup = "fixture.person@example.com";
  const password = "fixture-activation-password";
  const recoveryToken = "r".repeat(64);
  let recovery: unknown;
  let request: unknown;
  await page.route(`**${accountUrl}/password-reset/request`, route => {
    request = route.request().postDataJSON();
    return route.fulfill({ json: { ok: true } });
  });
  await page.route(`**${accountUrl}/password-reset/complete`, route => {
    recovery = route.request().postDataJSON();
    return route.fulfill({ json: { ok: true } });
  });

  await page.goto("/login");
  await expect(page.getByRole("link", { name: /sign up|register/i })).toHaveCount(0);
  await page.getByRole("link", { name: "Forgot password?" }).click();
  await expect(page).toHaveURL(/\/forgot-password$/);
  await page.getByLabel("MIST email address").fill(email);
  await page.getByLabel("Recovery email address").fill(backup);
  await page.getByRole("button", { name: "Request password reset" }).click();
  await expect.poll(() => request).toEqual({ email, kind: "email", destination: backup });
  await expect(page.getByRole("status")).toHaveText("If those details match a verified recovery method, you’ll receive password reset instructions.");

  await page.goto(`/reset-password#token=${recoveryToken}`);
  await expect(page.getByRole("heading", { name: "Reset your password", exact: true })).toBeVisible();
  await expect(page).toHaveURL(/\/reset-password$/);
  await page.getByLabel("New password", { exact: true }).fill(password);
  await page.getByLabel("Confirm password", { exact: true }).fill(password);
  await page.getByRole("button", { name: "Reset password", exact: true }).click();
  await expect.poll(() => recovery).toEqual({ token: recoveryToken, password });
});

test("profile manages masked recovery methods with password confirmation", async ({ page }) => {
  await stubWorkspace(page, ledBy);
  const primary = { id: "fixture-email", kind: "email", masked_destination: "l•••@getmistified.com", verified: true, preferred: true };
  const phone = { id: "fixture-phone", kind: "sms", masked_destination: "••••0100", verified: false, preferred: false };
  const password = "fixture-profile-password";
  let methods = [primary];
  await page.route(`**${accountUrl}/recovery`, route => route.fulfill({ json: {
    methods, allow_mist_email: true, sms_available: true,
  } }));
  await page.route(`**${accountUrl}/recovery/methods`, route => {
    expect(route.request().postDataJSON()).toEqual({ kind: "sms", destination: "+15550000100", password });
    methods = [primary, phone];
    return route.fulfill({ json: { ok: true } });
  });
  await page.route(`**${accountUrl}/recovery/methods/fixture-phone/preferred`, route => {
    expect(route.request().postDataJSON()).toEqual({ password });
    methods = [{ ...primary, preferred: false }, { ...phone, verified: true, preferred: true }];
    return route.fulfill({ json: { ok: true } });
  });
  await page.route(`**${accountUrl}/recovery/methods/fixture-email/remove`, route => {
    expect(route.request().postDataJSON()).toEqual({ password });
    methods = [{ ...phone, verified: true, preferred: true }];
    return route.fulfill({ json: { ok: true } });
  });

  await page.goto("/profile");
  await expect(page.getByRole("heading", { name: "Recovery methods" })).toBeVisible();
  const list = page.getByRole("list", { name: "Registered recovery methods" });
  const emailRow = list.getByRole("listitem").filter({ hasText: primary.masked_destination });
  const phoneRow = list.getByRole("listitem").filter({ hasText: phone.masked_destination });
  await expect(emailRow.getByRole("button", { name: "Remove", exact: true })).toBeDisabled();
  await page.getByRole("button", { name: "Add recovery method" }).click();
  await expect(page.getByText("Use a personal email or your individual MIST email.")).toBeVisible();
  await page.getByLabel("Recovery method", { exact: true }).selectOption("sms");
  await page.getByLabel("Recovery phone number").fill("+15550000100");
  await page.getByLabel("Current Atlas password").fill(password);
  await page.getByRole("button", { name: "Send verification" }).click();
  await expect(phoneRow.getByText("Unverified", { exact: true })).toBeVisible();
  await expect(emailRow.getByRole("button", { name: "Remove", exact: true })).toBeDisabled();
  methods = [primary, { ...phone, verified: true }];
  await page.reload();
  await phoneRow.getByRole("button", { name: "Make preferred" }).click();
  let dialog = page.getByRole("dialog");
  await expect(dialog).toHaveAccessibleName("Make preferred");
  await expect(dialog.getByRole("button", { name: "Make preferred" })).toBeVisible();
  await dialog.getByLabel("Current Atlas password").fill(password);
  await dialog.getByRole("button", { name: "Make preferred" }).click();
  await expect(phoneRow.getByText("Preferred", { exact: true })).toBeVisible();

  await emailRow.getByRole("button", { name: "Remove", exact: true }).click();
  dialog = page.getByRole("dialog", { name: "Remove recovery method" });
  await dialog.getByLabel("Current Atlas password").fill(password);
  await dialog.getByRole("button", { name: "Remove recovery method" }).click();
  await expect(emailRow).toHaveCount(0);
  await expect(phoneRow.getByRole("button", { name: "Remove", exact: true })).toBeDisabled();
});
