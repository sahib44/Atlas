import { expect, test } from "@playwright/test";
import { expectReadable, selectAppearance } from "./appearance-helpers";

test("appearance migrates, persists, and applies readable palettes to the workspace", async ({ page }) => {
  const password = process.env.SEED_PASSWORD;
  if (!password) throw new Error("SEED_PASSWORD is required");
  await page.addInitScript(() => localStorage.setItem("mist-theme", "dark"));
  await page.goto("/login");
  const root = page.locator("html");
  const appearance = page.getByRole("button", { name: "Appearance", exact: true });
  const options = page.getByRole("group", { name: "Theme", exact: true });
  await expect(root).toHaveAttribute("data-theme", "purple-dark");
  await appearance.press("Enter");
  await expect(options.getByRole("button", { pressed: true })).toHaveText("Purple Dark");
  await appearance.press("Tab");
  await expect(options.getByRole("button", { name: "Light", exact: true })).toBeFocused();
  await page.keyboard.press("Escape");
  await expect(options).toHaveCount(0);
  await expect(appearance).toBeFocused();

  const themes = [
    { label: "Light", value: "light", canvas: "rgb(247, 243, 253)", surface: "rgb(253, 251, 255)", sidebar: "rgb(246, 240, 252)" },
    { label: "Purple Dark", value: "purple-dark", canvas: "rgb(23, 20, 37)", surface: "rgb(37, 33, 55)", sidebar: "rgb(28, 23, 44)" },
    { label: "Dark", value: "dark", canvas: "rgb(22, 22, 24)", surface: "rgb(35, 35, 38)", sidebar: "rgb(27, 27, 30)" },
  ] as const;
  for (const { label, value, canvas, surface } of themes) {
    await selectAppearance(page, label);
    await page.reload();
    await expect(root).toHaveAttribute("data-theme", value);
    await expect(root).toHaveCSS("background-color", canvas);
    await expect(page.locator(".login-card")).toHaveCSS("background-color", surface);
    await expectReadable(page.getByText("Password", { exact: true }));
    await expectReadable(page.getByRole("button", { name: "Sign in" }));
  }
  await page.getByLabel("Email", { exact: true }).fill("director@example.com");
  await page.getByLabel("Password", { exact: true }).fill(password);
  await page.getByRole("button", { name: "Sign in" }).click();
  await expect(page.getByRole("heading", { name: "Operations overview" })).toBeVisible();
  await expect(root).toHaveAttribute("data-theme", "dark");

  const views = [
    ["/dashboard", "Operations overview", ".overview-metric"],
    ["/competitions", "Competitions", ".pilot-card"],
    ["/recruitment", "Judge Recruitment", ".judge-metric"],
    ["/judges", "Judge Directory", ".judge-metric"],
    ["/campaigns", "Outreach campaigns", 'input[aria-label="Search outreach campaigns"]'],
    ["/tickets", "Tickets", ".ticket-pane"],
    ["/imports", "Imports", "header.topbar"],
    ["/duplicates", "Duplicate review", "header.topbar"],
    ["/outbox", "Sandbox outbox", ".outbox-reassurance"],
    ["/audit", "Audit trail", "header.topbar"],
    ["/profile", "Profile", "header.topbar"],
  ];
  for (const [path, title, surface] of views) {
    await page.goto(path);
    const heading = page.getByRole("heading", { name: title, exact: true });
    await expect(heading).toBeVisible();
    await expect(page.locator(surface).first()).toBeVisible();
    for (const { label, value, sidebar } of themes) {
      await selectAppearance(page, label);
      await expect(root).toHaveAttribute("data-theme", value);
      await expect(page.locator(".sidebar")).toHaveCSS("background-color", sidebar);
      await expectReadable(heading);
      await expectReadable(page.getByRole("link", { name: "Overview", exact: true }));
      if (path === "/tickets") await expectReadable(page.getByRole("searchbox", { name: "Search tickets" }), true);
      if (path === "/campaigns") await expectReadable(page.getByRole("textbox", { name: "Search outreach campaigns" }), true);
    }
  }

  await page.setViewportSize({ width: 390, height: 844 });
  for (const { label, surface } of themes) {
    await selectAppearance(page, label);
    await page.getByRole("button", { name: /^Notifications/ }).click();
    const dialog = page.getByRole("dialog", { name: "Notifications" });
    await expect(dialog).toHaveCSS("background-color", surface);
    await expectReadable(dialog.getByRole("heading"));
    await dialog.getByRole("button", { name: "Close dialog" }).click();
  }
});
