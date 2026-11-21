import { expect, test, type Page } from "@playwright/test";
import { randomUUID } from "node:crypto";

const headers = { "X-MIST-Request": "1" };

async function login(page: Page, email: string) {
  const password = process.env.SEED_PASSWORD;
  if (!password) throw new Error("SEED_PASSWORD is required");
  await page.goto("/login");
  await page.getByLabel("Email", { exact: true }).fill(email);
  await page.getByLabel("Password", { exact: true }).fill(password);
  await page.getByRole("button", { name: "Sign in", exact: true }).click();
  await expect(page.getByRole("heading", { name: "Operations overview", exact: true })).toBeVisible();
}

test("a head can copy and send an internal draft explicitly approved for public reply", async ({ page, browser, baseURL }) => {
  const reviewer = await browser.newContext({ baseURL });
  const chair = await reviewer.newPage();
  try {
    await login(page, "competitions@example.com");
    await login(chair, "chair@example.com");
    const title = "Reuse an approved reply " + randomUUID().slice(0, 8);
    const exact = "Please upload a PDF of the project description.";
    const privateContext = "Internal scheduling concern. Keep private.";
    const ticketResponse = await page.request.post("/api/tickets", {
      headers,
      data: { requester_name: "Fixture Requester", requester_email: "fixture-ticket@example.org",
        subject: title, body: "Which document should we upload?", request_id: randomUUID() },
    });
    expect(ticketResponse.ok()).toBe(true);
    const ticket = await ticketResponse.json();
    const path = "/tickets/" + ticket.id;
    const noteResponse = await page.request.post("/api" + path + "/notes", {
      headers, data: { body: privateContext, request_id: randomUUID() },
    });
    expect(noteResponse.ok()).toBe(true);

    await page.goto(path);
    await page.getByRole("button", { name: "Internal note", exact: true }).click();
    await page.getByLabel("Private internal note", { exact: true }).fill(exact);
    await page.getByRole("button", { name: "Request chair approval", exact: true }).click();
    await expect(page.getByRole("button", { name: "Awaiting chair approval", exact: true })).toBeVisible();
    await expect(page.getByRole("button", { name: "Load as Public Reply", exact: true })).toHaveCount(0);

    await chair.goto(path);
    await chair.getByRole("button", { name: "Approve for public reply", exact: true }).click();
    const useApproved = page.getByRole("button", { name: "Load as Public Reply", exact: true });
    await expect(useApproved).toBeVisible();
    await page.getByRole("button", { name: "Save private internal note", exact: true }).click();
    await expect(page.getByText("Private note saved.", { exact: true })).toBeVisible();
    await expect(useApproved).toBeVisible();
    await useApproved.click();

    const subject = page.getByLabel("Public reply subject", { exact: true });
    const body = page.getByLabel("Public reply body", { exact: true });
    const send = page.getByRole("button", { name: "Send public sandbox reply", exact: true });
    await expect(subject).toHaveValue(`Re: ${ticket.public_number} — ${title}`);
    await expect(body).toHaveValue(exact);
    await expect(send).toBeEnabled();
    expect((await (await page.request.get(`/api/outbox?ticket_id=${ticket.id}`)).json()).total).toBe(0);

    await subject.fill("Different subject");
    await expect(send).toBeDisabled();
    await expect(page.getByRole("button", { name: "Request chair approval", exact: true })).toBeEnabled();
    await useApproved.click();
    await body.fill(exact + " Updated.");
    await expect(send).toBeDisabled();
    await useApproved.click();
    await expect(send).toBeEnabled();
    await send.click();

    await expect(page.locator(".ticket-email.staff_reply")).toHaveCount(1);
    await expect(page.locator(".ticket-email.staff_reply")).toContainText(exact);
    await expect(page.locator(".ticket-email.staff_reply")).not.toContainText(privateContext);
    await expect(page.locator(".ticket-inline-note").filter({ hasText: exact })).toBeVisible();
    await expect(page.locator(".ticket-inline-note").filter({ hasText: privateContext })).toBeVisible();
    await expect(useApproved).toHaveCount(0);

    const outbox = await (await page.request.get(`/api/outbox?ticket_id=${ticket.id}`)).json();
    expect(outbox.items).toHaveLength(1);
    expect(outbox.items[0].body_text).toBe(exact);
  } finally {
    await reviewer.close();
  }
});
