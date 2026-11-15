import { expect, test } from "@playwright/test";
import { readJson, writeJson } from "./recruitment-helpers";

test("an open ticket stops displaying private content when access is revoked", async ({ page, playwright, baseURL }) => {
  const director = await playwright.request.newContext({ baseURL });
  try {
    const password = process.env.SEED_PASSWORD!;
    await writeJson(director, "/auth/login", { email: "director@example.com", password });
    await writeJson(page.request, "/auth/login", { email: "tech@example.com", password });
    const tech = await readJson(page.request, "/auth/me");
    const setAccess = (permissions: string[]) => writeJson(director, `/users/${tech.id}`, {
      role: tech.role, is_active: true, permissions,
    }, "PATCH");
    await setAccess(["private_tickets"]);
    try {
      const ticket = await writeJson(director, "/tickets", {
        requester_name: "Sample Revocation Tester", requester_email: "revocation@example.org",
        subject: "Sample restricted ticket", body: "Confidential ticket content for this test.",
      });
      await page.goto(`/tickets/${ticket.id}`);
      const content = page.getByText("Confidential ticket content for this test.", { exact: true });
      await expect(content).toBeVisible();
      await setAccess([]);
      await expect(content).toHaveCount(0);
      await expect(page.getByRole("heading", { name: "Access restricted" })).toBeVisible();
    } finally {
      await setAccess(tech.permissions);
    }
  } finally {
    await director.dispose();
  }
});
