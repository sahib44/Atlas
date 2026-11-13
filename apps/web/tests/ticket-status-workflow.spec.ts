import { test, expect } from "@playwright/test";

test("heads request chair approval, send status replies, organize notes and return from outbox", async ({ page, browser, baseURL }) => {
  page.setDefaultTimeout(15000);
  const password = process.env.SEED_PASSWORD;
  if (!password) throw new Error("SEED_PASSWORD is required");
  const chairContext = await browser.newContext({ baseURL });
  const chair = await chairContext.newPage();
  const login = async (target: typeof page, email: string) => {
    await target.goto("/login");
    await target.getByLabel("Email", { exact: true }).fill(email);
    await target.getByLabel("Password", { exact: true }).fill(password);
    await target.getByRole("button", { name: "Sign in", exact: true }).click();
    await expect(target.getByRole("heading", { name: "Operations overview" })).toBeVisible();
  };
  await login(page, "competitions@example.com");
  await login(chair, "chair@example.com");
  await page.goto("/tickets/new");
  const title = `Ticket status review ${Date.now()}`;
  await page.getByLabel("Requester name", { exact: true }).fill("Sample Requester");
  await page.getByLabel("Requester email", { exact: true }).fill("status-review@example.org");
  await page.getByLabel("Subject", { exact: true }).fill(title);
  await page.getByLabel("Message body", { exact: true }).fill("Please help me understand a competition rule.");
  await page.getByRole("button", { name: "Create simulated ticket" }).click();
  await expect(page.getByRole("heading", { name: title, exact: true })).toBeVisible();
  const ticketUrl = page.url();
  const ticketId = ticketUrl.split("/tickets/")[1];
  const ticket = await (await page.request.get(`/api/tickets/${ticketId}`)).json();
  const newAlert = (await (await page.request.get("/api/notifications")).json()).items.find((item: any) => item.kind === "ticket" && item.ticket_id === ticketId);
  await page.locator(".topbar").getByRole("button", { name: /Notifications/ }).click();
  await page.getByRole("dialog").locator(".notification-item").filter({ hasText: title }).click();
  expect((await (await page.request.get("/api/notifications")).json()).items.find((item: any) => item.id === newAlert.id).read_at).toBeNull();
  await page.getByRole("button", { name: "Mark In Progress", exact: true }).click();
  await expect(page.locator(".ticket-thread-title .badge")).toContainText("In Progress");
  expect((await (await page.request.get("/api/notifications")).json()).items.find((item: any) => item.id === newAlert.id).read_at).toBeTruthy();
  await page.goto("/tickets?view=waiting");
  await expect(page.getByRole("region", { name: "Ticket inbox" }).getByRole("link", { name: new RegExp(title) })).toBeVisible();
  await page.getByRole("link", { name: new RegExp(title) }).click();
  await page.getByLabel("Public reply body", { exact: true }).fill("Can you tell me which rule you mean?");
  const composer = page.locator(".composer");
  const emailThread = page.getByLabel("Ticket messages", { exact: true });
  const replyBounds = await composer.boundingBox();
  const replyThreadBounds = await emailThread.boundingBox();
  const replyFieldBounds = await page.getByLabel("Public reply body", { exact: true }).boundingBox();
  await page.getByRole("button", { name: "Internal note", exact: true }).click();
  expect(await composer.boundingBox()).toEqual(replyBounds);
  expect(await emailThread.boundingBox()).toEqual(replyThreadBounds);
  expect(await page.getByLabel("Private internal note", { exact: true }).boundingBox()).toEqual(replyFieldBounds);
  await page.getByRole("button", { name: "Public reply", exact: true }).click();
  await page.getByLabel("Public reply body", { exact: true }).clear();
  for (const text of ["First finding from the rules guide.", "Most recent finding from the chair."]) {
    await page.getByRole("button", { name: "Internal note", exact: true }).click();
    await page.getByLabel("Private internal note", { exact: true }).fill(text);
    await page.getByRole("button", { name: "Save private internal note", exact: true }).click();
    await expect(page.locator(".ticket-inline-note").last()).toContainText(text);
  }
  await page.getByLabel("Ticket status", { exact: true }).selectOption("waiting_on_requester");
  await expect(page.locator(".composer")).toHaveClass(/reply-required/);
  await expect(page.getByLabel("Public reply body", { exact: true })).toHaveJSProperty("required", true);
  await expect(page.getByLabel("Public reply body", { exact: true })).toBeFocused();
  await expect(page.getByRole("button", { name: "Update status", exact: true })).toBeDisabled();
  await page.getByLabel("Public reply body", { exact: true }).fill("Which part of the rule needs clarification?");
  await expect(page.getByRole("button", { name: "Update status and send reply", exact: true })).toBeDisabled();
  await page.screenshot({ path: "test-results/ticket-required-reply.png", fullPage: true });
  await page.getByRole("button", { name: "Save draft", exact: true }).click();
  await expect(page.getByRole("status").filter({ hasText: "Draft saved in this browser session." })).toBeVisible();
  await page.getByRole("link", { name: "View ticket outbox", exact: true }).click();
  await expect(page.getByRole("heading", { name: "Sandbox outbox", exact: true })).toBeVisible();
  const icon = await page.locator(".outbox-reassurance > svg").boundingBox();
  const text = await page.locator(".outbox-reassurance > p").boundingBox();
  expect(icon).not.toBeNull(); expect(text).not.toBeNull();
  expect(text!.x - (icon!.x + icon!.width)).toBeGreaterThanOrEqual(0);
  expect(text!.x - (icon!.x + icon!.width)).toBeLessThan(20);
  await page.getByRole("button", { name: "Go back", exact: true }).click();
  await expect(page).toHaveURL(ticketUrl + "?view=waiting");
  await expect(page.getByLabel("Public reply body", { exact: true })).toHaveValue("Which part of the rule needs clarification?");
  await page.getByRole("button", { name: "Request chair approval", exact: true }).click();
  await expect(page.getByRole("button", { name: "Awaiting chair approval", exact: true })).toBeVisible();
  await chair.locator(".topbar").getByRole("button", { name: /Notifications/ }).click();
  await chair.getByRole("dialog").locator(".notification-item").filter({ hasText: `Chair approval requested: ${ticket.public_number}` }).click();
  await expect(chair.getByRole("heading", { name: title, exact: true })).toBeVisible();
  await expect(chair.locator(".ticket-review")).toContainText("Which part of the rule needs clarification?");
  await expect(chair.locator(".ticket-inline-note").last()).toContainText("Most recent finding from the chair.");
  await chair.getByRole("button", { name: "Approve draft", exact: true }).click();
  expect((await (await page.request.get(`/api/outbox?ticket_id=${ticketId}`)).json()).total).toBe(0);
  await page.locator(".topbar").getByRole("button", { name: /Notifications/ }).click();
  await page.getByRole("dialog").locator(".notification-item").filter({ hasText: `Draft approved: ${ticket.public_number}` }).click();
  await expect(page.getByRole("button", { name: "Update status and send reply", exact: true })).toBeEnabled();
  await page.getByRole("button", { name: "Update status and send reply", exact: true }).click();
  await expect(page.locator(".ticket-thread-title .badge")).toContainText("Waiting On Requester");
  await expect(page.locator(".ticket-email.staff_reply")).toContainText("Which part of the rule needs clarification?");
  await expect.poll(async () => (await (await page.request.get(`/api/tickets/${ticketId}`)).json()).auto_close_at).toBeTruthy();
  await page.getByLabel("Ticket status", { exact: true }).selectOption("waiting_on_organizer");
  await expect(page.getByLabel("Private internal note", { exact: true })).toBeFocused();
  await expect(page.getByLabel("Private internal note", { exact: true })).toHaveJSProperty("required", true);
  await expect(page.getByRole("button", { name: "Update status", exact: true })).toBeDisabled();
  await page.getByLabel("Private internal note", { exact: true }).fill("The team will check the response against the rules.");
  await page.getByRole("button", { name: "Update status and save note", exact: true }).click();
  await expect(page.locator(".ticket-thread-title .badge")).toContainText("Awaiting Organizer");
  await expect(page.locator(".ticket-inline-note").last()).toContainText("The team will check the response against the rules.");
  await page.getByLabel("Ticket status", { exact: true }).selectOption("resolved");
  await expect(page.getByLabel("Resolution note", { exact: true })).toHaveJSProperty("required", true);
  await expect(page.getByLabel("Public reply body", { exact: true })).toHaveJSProperty("required", false);
  await page.getByLabel("Resolution note", { exact: true }).fill("The requester has the correct rule and no further action is needed.");
  await page.getByRole("button", { name: "Update status", exact: true }).click();
  await expect(page.locator(".ticket-thread-title .badge")).toContainText("Resolved");
  await expect(page.locator(".ticket-inline-note").last()).toContainText("The requester has the correct rule");
  expect((await (await page.request.get(`/api/outbox?ticket_id=${ticketId}`)).json()).total).toBe(1);
  await page.screenshot({ path: "test-results/ticket-status-resolved.png", fullPage: true });
  const inbox = page.getByRole("region", { name: "Ticket inbox" });
  const details = page.getByRole("complementary", { name: "Ticket details and activity" });
  await details.getByRole("button", { name: "Activity", exact: true }).click();
  await expect(details).toContainText("Ticket status: resolved");
  await page.setViewportSize({ width: 1024, height: 800 });
  await expect(inbox).toBeVisible();
  await expect(details).toBeHidden();
  await page.getByRole("button", { name: "Details", exact: true }).click();
  await expect(details).toBeVisible();
  await details.getByRole("button", { name: "Back to email" }).click();
  await page.setViewportSize({ width: 390, height: 844 });
  await expect(inbox).toBeHidden();
  await page.getByRole("link", { name: "Inbox", exact: true }).click();
  await expect(inbox).toBeVisible();
  await inbox.getByRole("button", { name: /^All\b/ }).click();
  await inbox.getByRole("searchbox", { name: "Search tickets" }).fill(title);
  await inbox.getByRole("link", { name: new RegExp(title) }).click();
  await expect(page.getByRole("heading", { name: title })).toBeVisible();
  await expect.poll(() => page.evaluate(() => document.documentElement.scrollWidth <= innerWidth)).toBe(true);
  await chairContext.close();
});

test("ticket inbox and correspondence fit desktop, tablet and phone", async ({ page }) => {
  page.setDefaultTimeout(15000);
  const password = process.env.SEED_PASSWORD;
  if (!password) throw new Error("SEED_PASSWORD is required");
  await page.goto("/login");
  await page.getByLabel("Email", { exact: true }).fill("chair@example.com");
  await page.getByLabel("Password", { exact: true }).fill(password);
  await page.getByRole("button", { name: "Sign in", exact: true }).click();
  await expect(page.getByRole("heading", { name: "Operations overview" })).toBeVisible();

  const cases = [
    ["Culinary Arts – Ingredient Rules", "Samira Vale", "Salam MIST NJ team,\n\nAre store-bought base ingredients permitted in Culinary Arts? The rules say the sauce must be prepared on site. Could you clarify whether that applies to broth or tomato paste?\n\nJazakAllah khair,\nSamira Vale\nTeam Captain, Evergreen Academy"],
    ["Registration issue", "Eli Brooks", "Hello Competitions team,\n\nOur team cannot find the registration confirmation. Could you direct us to the appropriate committee?\n\nThank you,\nEli Brooks"],
    ["Video submission format", "Maya Chen", "Hello,\n\nCould you confirm the required file format for the early submission?\n\nBest,\nMaya Chen"],
    ["Debate – Rule clarification", "Nora Patel", "Hello,\n\nCould you confirm how long each opening statement should be?\n\nBest regards,\nNora Patel"],
    ["Team roster update", "Omar Reed", "Hello,\n\nWe need to make a change to our roster. Please let us know the deadline.\n\nThank you,\nOmar Reed"],
  ];
  const records = [];
  const headers = { "X-MIST-Request": "1" };
  for (const [index, [subject, name, body]] of cases.entries()) {
    const result = await page.request.post("/api/tickets", { headers, data: {
      requester_name: name, requester_email: `ticket-demo-${index}@example.org`, subject, body,
    } });
    expect(result.ok()).toBe(true);
    records.push(await result.json());
  }
  const [ticket, registration, video, debate, roster] = records;
  for (const [record, data] of [
    [registration, { status: "in_progress" }],
    [video, { status: "waiting_on_organizer", internal_note: "Checking the submission guide." }],
    [debate, { status: "waiting_on_requester", reply_body: "Which debate format is your team entering?" }],
    [roster, { status: "resolved", resolution_note: "Shared the roster update deadline." }],
  ] as const) {
    expect((await page.request.post(`/api/tickets/${record.id}/status`, { headers, data })).ok()).toBe(true);
  }
  expect((await page.request.post(`/api/tickets/${ticket.id}/notes`, { headers, data: {
    body: "I’ll reply with clarification from the Culinary Arts rubric.", request_id: crypto.randomUUID(),
  } })).ok()).toBe(true);
  const detailPath = `/tickets/${ticket.id}`;
  await page.setViewportSize({ width: 1672, height: 941 });
  await page.goto(detailPath);
  const inbox = page.getByRole("region", { name: "Ticket inbox" });
  const email = page.getByRole("region", { name: "Email conversation" });
  const details = page.getByRole("complementary", { name: "Ticket details and activity" });
  await expect(inbox.getByRole("link").filter({ hasText: ticket.public_number })).toHaveAttribute("aria-current", "true");
  await expect(email.getByText("Team Captain, Evergreen Academy", { exact: false })).toBeVisible();
  await expect(email.locator(".ticket-inline-note")).toContainText("Culinary Arts rubric");
  await expect(details.getByText("No pending approvals")).toBeVisible();
  await expect(details.getByRole("button", { name: "Close ticket" })).toHaveCount(0);
  await expect(page.getByRole("button", { name: "Request chair approval" })).toHaveCount(0);
  const panels = await Promise.all([inbox.boundingBox(), email.boundingBox(), details.boundingBox()]);
  expect(panels[0]!.x + panels[0]!.width).toBeLessThan(panels[1]!.x);
  expect(panels[1]!.x + panels[1]!.width).toBeLessThan(panels[2]!.x);
  await page.screenshot({ path: "test-results/phase4-tickets-light.png", animations: "disabled" });
  await page.getByRole("button", { name: "Switch to dark mode" }).click();
  await page.screenshot({ path: "test-results/phase4-tickets-dark.png", animations: "disabled" });
  await page.getByRole("button", { name: "Switch to light mode" }).click();

  await inbox.getByRole("button", { name: /^Overdue/ }).click();
  await expect(inbox.getByRole("button", { name: /^Overdue/ })).toHaveAttribute("aria-pressed", "true");
  await inbox.getByRole("button", { name: /^All\b/ }).click();
  await expect(inbox.getByRole("button", { name: /^All\b/ })).toHaveAttribute("aria-pressed", "true");
  await inbox.getByRole("searchbox").fill("Ingredient Rules");
  await expect(inbox.getByRole("link").filter({ hasText: ticket.public_number })).toBeVisible();
  await expect(inbox.getByRole("link", { name: /Registration issue/ })).toHaveCount(0);
  await inbox.getByRole("searchbox").clear();
  await page.setViewportSize({ width: 1024, height: 768 });
  await expect(inbox).toBeVisible();
  await expect(details).toBeHidden();
  await page.screenshot({ path: "test-results/phase4-tickets-tablet.png", animations: "disabled" });

  await page.setViewportSize({ width: 390, height: 844 });
  await expect(inbox).toBeHidden();
  await expect(email).toBeVisible();
  const composer = page.locator(".composer");
  const replyBounds = await composer.boundingBox();
  await page.getByRole("button", { name: "Internal note", exact: true }).click();
  expect(await composer.boundingBox()).toEqual(replyBounds);
  await page.getByRole("button", { name: "Public reply", exact: true }).click();
  await page.screenshot({ path: "test-results/phase4-tickets-mobile-detail.png", animations: "disabled" });
  await email.getByRole("link", { name: "Inbox", exact: true }).click();
  await expect(inbox).toBeVisible();
  await expect(email).toBeHidden();
  await page.screenshot({ path: "test-results/phase4-tickets-mobile-inbox.png", animations: "disabled" });
  await expect.poll(() => page.evaluate(() => document.documentElement.scrollWidth <= innerWidth)).toBe(true);
});
