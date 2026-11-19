import { expect, test } from "@playwright/test";
import { readJson, writeJson, mutationHeaders } from "./recruitment-helpers";

test("an open import reflects review and commit by another client", async ({ page }) => {
  await writeJson(page.request, "/auth/login", {
    email: "director@example.com", password: process.env.SEED_PASSWORD!,
  });
  const uploaded = await page.request.post("/api/imports", {
    headers: mutationHeaders,
    multipart: { file: {
      name: "refresh.csv", mimeType: "text/csv",
      buffer: Buffer.from("Name,Email\nImport Refresh,import-refresh@example.org\nImport Refresh,import-refresh@example.org\n"),
    } },
  });
  expect(uploaded.ok()).toBeTruthy();
  const batch = await uploaded.json();
  const path = `/imports/${batch.id}`;
  await writeJson(page.request, `${path}/mapping`, { mapping: batch.mapping }, "PUT");
  await writeJson(page.request, `${path}/analyze`);
  await page.goto(path);
  const continueButton = page.getByRole("button", { name: "Continue to commit" });
  await expect(continueButton).toBeDisabled();
  const { items } = await readJson(page.request, `/duplicates?batch_id=${batch.id}`);
  await writeJson(page.request, `/duplicates/${items[0].id}/resolve`, { decision: "merge", confirmed: true });
  await expect(continueButton).toBeEnabled({ timeout: 8000 });
  await writeJson(page.request, `${path}/commit`, { confirmed: true });
  await expect(page.getByRole("heading", { name: "Import complete" })).toBeVisible({ timeout: 8000 });
  await page.reload();
  await expect(page.getByRole("heading", { name: "Import complete" })).toBeVisible();
});
