import { expect, test } from "@playwright/test";
import { expectReadable, selectAppearance } from "./appearance-helpers";
import { approveNomination, readJson, writeJson } from "./recruitment-helpers";

test("chair reviews a member's candidate from the overview before confirmation and withdrawal", async ({ page, browser, baseURL }) => {
  const password = process.env.SEED_PASSWORD;
  if (!password) throw new Error("SEED_PASSWORD is required");
  const chairPage = await browser.newPage({ baseURL });
  const chair = chairPage.request;
  try {
    await chairPage.goto("/login");
    const logo = chairPage.locator(".login-identity img");
    const logoPath = await logo.getAttribute("src");
    expect(logoPath).toBe("/brand/atlas-stacked-compact-light.svg");
    await expect.poll(() => logo.evaluate((img: HTMLImageElement) => img.naturalWidth)).toBeGreaterThan(0);
    await chairPage.getByLabel("Email", { exact: true }).fill("chair@example.com");
    await chairPage.getByLabel("Password", { exact: true }).fill(password);
    await chairPage.getByRole("button", { name: "Sign in", exact: true }).click();
    await expect(chairPage.locator(".workspace-copy")).toContainText("Competitions Chair");
    await expect(chairPage.getByRole("link", { name: "Judge Recruitment", exact: true })).toBeVisible();
    await expect(chairPage.getByRole("link", { name: "Judge Directory", exact: true })).toBeVisible();
    await page.goto("/login");
    await page.getByLabel("Email", { exact: true }).fill("competitions@example.com");
    await page.getByLabel("Password", { exact: true }).fill(password);
    await page.getByRole("button", { name: "Sign in", exact: true }).click();
    await expect(page.getByRole("heading", { name: "Operations overview" })).toBeVisible();
    await expect(page.getByRole("link", { name: /Judge requests awaiting approval/ })).toHaveCount(0);
    // Use the existing catalog and create a disposable pilot for an independent roster.
    const compResponse = await chair.post("/api/competitions", {
      headers: { "X-MIST-Request": "1" },
      multipart: { name: "Phase Three Review", event_year: "2027", category_slug: "arts" },
    });
    expect(compResponse.ok()).toBe(true);
    const competition = await compResponse.json();
    await page.goto(`/competitions/arts/${competition.id}`);
    await page.getByRole("link", { name: "Add judge", exact: true }).click();
    const dialog = page.getByRole("dialog", { name: "Add candidate" });
    await expect(dialog.getByLabel("Intended competition")).toHaveValue(competition.id);
    await dialog.getByLabel("Full name").fill("Nadia Sample");
    await dialog.getByLabel("Email", { exact: true }).fill("nadia.sample@example.org");
    await dialog.getByLabel("Phone (optional)").fill("(201) 555-0112");
    await dialog.getByLabel("Qualifications").fill("Visual arts instructor with eight seasons of regional judging experience.");
    await dialog.getByLabel("Notes (optional)").fill("Available for the morning round. Previously judged at MIST NJ.");
    await dialog.getByLabel("Prior MIST involvement (optional)").fill("Returning judge (2025)");
    await dialog.getByRole("button", { name: "Submit for chair approval" }).click();
    await expect(page.getByText("Pending chair approval", { exact: true })).toBeVisible();
    const candidateId = new URL(page.url()).searchParams.get("candidate");
    expect((await readJson(chair, "/notifications")).items.some((item: any) => item.href.includes(candidateId))).toBe(true);
    const reviewQueue = chairPage.getByRole("link", { name: /Judge requests awaiting approval/ });
    await expect(reviewQueue).toContainText(/[1-9]\d*/);
    await reviewQueue.click();
    await expect(chairPage).toHaveURL(/\/recruitment\?view=requests&event_year=2027/);
    await chairPage.getByRole("button", { name: "Nadia Sample", exact: true }).click();
    await chairPage.getByRole("button", { name: "Approve request", exact: true }).click();
    await expect(chairPage.getByRole("link", { name: "Start Outreach" })).toBeVisible();
    await expect(chairPage).toHaveURL(/stage=approved/);
    await chairPage.getByRole("button", { name: "Return to Prospect" }).click();
    await chairPage.getByRole("button", { name: "Reopen request" }).click();
    await expect(chairPage).toHaveURL(/stage=prospect/);
    await chairPage.getByRole("button", { name: "Reject request", exact: true }).click();
    await expect(chairPage.getByRole("dialog", { name: "Reject judge request" })).toBeVisible();
    expect((await readJson(chair, `/recruitment/${candidateId}`)).review_status).toBe("pending");
    await chairPage.getByRole("button", { name: "Confirm rejection", exact: true }).click();
    await expect(chairPage).toHaveURL(/view=archive/);
    await page.getByRole("button", { name: "Move back to Recruitment" }).click();
    await page.getByRole("button", { name: "Reopen request" }).click();
    await expect(page.getByText("Pending chair approval", { exact: true })).toBeVisible();
    await approveNomination(chair, await readJson(chair, `/recruitment/${candidateId}`));
    await expect(page.getByRole("link", { name: "Start Outreach" })).toBeVisible();
    const response = page.getByLabel("Response", { exact: true });
    await response.selectOption("declined");
    await expect(page.getByRole("button", { name: "Save decline" })).toBeVisible();
    expect((await readJson(page.request, `/recruitment/${candidateId}`)).status).toBe("to_contact");
    await response.selectOption("to_contact");
    await expect(page.getByRole("button", { name: "Save decline" })).toHaveCount(0);
    await page.getByLabel("Response", { exact: true }).selectOption("accepted");
    await expect(page.getByRole("button", { name: "Confirm as Judge" })).toBeVisible();

    // Populate adjacent rows in the disposable test database for visual comparison.
    const names = ["Avery Brooks", "Dylan Park", "Iris Torres", "Samir Vale", "Leah Chen"];
    const catalog = (await readJson(chair, "/competitions?event_year=2027&page_size=100")).items;
    const visualCompetitions = ["2D Art", "3D Art", "Photography", "Fashion Design", "Culinary Arts"]
      .map(name => catalog.find((row: any) => row.name === name) || competition);
    for (const [index, name] of names.entries()) {
      const row = await writeJson(chair, "/recruitment", {
        competition_id: visualCompetitions[index].id, name, email: `visual-candidate-${index}@example.org`,
        qualifications: "Community competition reviewer.", phone: "(201) 555-0103",
      });
      if (index % 2 === 0) await approveNomination(chair, row);
    }
    await writeJson(chair, `/recruitment/${candidateId}/response`, { status: "awaiting_response" });
    await writeJson(chair, `/recruitment/${candidateId}/response`, { status: "accepted" });
    await page.goto(`/recruitment?event_year=2027&candidate=${candidateId}`);
    await expect(page.getByRole("heading", { name: "Judge Request Summary" })).toBeVisible();
    await page.setViewportSize({ width: 1536, height: 560 });
    await page.screenshot({ path: "test-results/recruitment-light.png" });
    await selectAppearance(page, "Purple Dark");
    await page.screenshot({ path: "test-results/recruitment-dark.png" });

    await page.getByRole("button", { name: "Confirm as Judge" }).click();
    await expect(page.getByRole("button", { name: "Nadia Sample", exact: true })).toHaveCount(0);
    const directory = await readJson(page.request, `/judge-directory?event_year=2027&competition_id=${competition.id}`);
    expect(directory.items).toHaveLength(1);
    const assignment = directory.items[0];
    for (const [index, name] of ["Celeste Rivera", "Jonah Ellis", "Maya Bell", "Ruben Flores"].entries()) {
      const row = await writeJson(chair, "/recruitment", {
        competition_id: visualCompetitions[index].id, name, email: `visual-confirmed-${index}@example.org`,
        qualifications: "Art program mentor and competition judge.", phone: "(973) 555-0134",
      });
      await approveNomination(chair, row);
      await writeJson(chair, `/recruitment/${row.id}/response`, { status: "accepted" });
      await writeJson(chair, `/recruitment/${row.id}/confirm`);
    }
    await page.goto(`/judges?event_year=2027&profile=${assignment.id}`);
    const panel = page.getByRole("complementary", { name: "Details for Nadia Sample" });
    await expect(panel.getByText("Confirmed Assignment", { exact: true })).toBeVisible();
    const earlierTournament = await chair.post("/api/competitions", {
      headers: { "X-MIST-Request": "1" },
      multipart: { name: "Directory Year Check", event_year: "2026", category_slug: "arts" },
    });
    expect(earlierTournament.ok()).toBe(true);
    const tournament = page.getByRole("combobox", { name: "Tournament", exact: true });
    await tournament.selectOption("2026");
    await expect(page.getByText("Confirmed judges and assignments for the 2026 tournament.")).toBeVisible();
    await expect(panel).toHaveCount(0);
    await tournament.selectOption("2027");
    await expect(page.getByText("Confirmed judges and assignments for the 2027 tournament.")).toBeVisible();
    await page.getByRole("button", { name: "Nadia Sample", exact: true }).click();
    await page.setViewportSize({ width: 1752, height: 897 });
    await page.screenshot({ path: "test-results/directory-dark.png" });
    await selectAppearance(page, "Dark");
    await expect(panel).toHaveCSS("background-color", "rgb(35, 35, 38)");
    await expectReadable(panel.getByText("Confirmed Assignment", { exact: true }));
    await expectReadable(page.getByRole("row", { name: /Nadia Sample/ }).getByRole("button", { name: "Nadia Sample", exact: true }));
    await selectAppearance(page, "Light");
    await page.screenshot({ path: "test-results/directory-light.png" });
    await page.getByRole("button", { name: "Nadia Sample", exact: true }).click();
    await expect(panel).toHaveCount(0);
    await expect(page.getByRole("link", { name: "Phase Three Review (Arts)", exact: true })).toHaveAttribute("href", `/competitions/arts/${competition.id}`);
    await page.getByRole("button", { name: "Nadia Sample", exact: true }).click();
    await panel.getByRole("button", { name: "Edit qualifications and notes" }).click();
    const details = page.getByRole("dialog", { name: "Edit qualifications and notes" });
    await details.getByLabel("Qualifications").fill("Updated arts judging experience.");
    await details.getByLabel("Notes", { exact: true }).fill("Updated availability.");
    await details.getByRole("button", { name: "Save", exact: true }).click();
    await expect(details).toHaveCount(0);
    await expect(panel.getByText("Updated arts judging experience.")).toBeVisible();
    await panel.getByRole("button", { name: "History", exact: true }).click();
    await expect(panel.getByText("Updated qualifications and notes", { exact: true })).toBeVisible();
    await panel.getByRole("button", { name: "Profile", exact: true }).click();
    await panel.getByRole("link", { name: "Email judge", exact: true }).click();
    await expect(page.getByLabel("Selected recipients")).toContainText("Nadia Sample");
    await expect(page.getByLabel("Subject", { exact: true })).toHaveValue("");
    await expect(page.getByLabel("Email body")).toHaveValue("");
    await expect(page.getByLabel("Event name")).toHaveValue("");
    await page.getByLabel("Message template").selectOption("judge-invitation");
    await page.getByRole("button", { name: "Use template in blank message" }).click();
    await expect(page.getByLabel("Subject", { exact: true })).toHaveValue("An invitation to judge {{event_name}}");
    await page.goto(`/judges?event_year=2027&profile=${assignment.id}`);
    await page.setViewportSize({ width: 390, height: 844 });
    await expect(page.getByRole("button", { name: "Go back" })).toBeVisible();
    await expect(page.getByRole("button", { name: /Notifications/ })).toBeVisible();
    await expect(panel.getByRole("button", { name: "Unconfirm", exact: true })).toBeVisible();
    await expect.poll(() => page.evaluate(() => document.documentElement.scrollWidth <= window.innerWidth)).toBe(true);
    await panel.getByRole("button", { name: "Unconfirm", exact: true }).click();
    await page.getByRole("dialog", { name: "Unconfirm judge" }).getByRole("button", { name: "Unconfirm assignment" }).click();
    await expect(panel).toHaveCount(0);
    expect((await readJson(page.request, `/competitions/${competition.id}`)).confirmed_count).toBe(0);
    await page.goto(`/competitions/${competition.category_slug}/${competition.id}`);
    const withdrawnNomination = page.locator(".pilot-judge").filter({ hasText: "Nadia Sample" });
    await expect(withdrawnNomination).toContainText("Withdrawn");
    await expect(withdrawnNomination.getByLabel("Response", { exact: true })).toBeDisabled();
    await page.goto("/recruitment?event_year=2027&view=archive");
    await expect(page.getByRole("row").filter({ hasText: "Nadia Sample" })).toContainText("Withdrawn");
    await page.getByRole("button", { name: "Nadia Sample", exact: true }).click();
    await expect(page.getByRole("heading", { name: "Judge Request Summary" })).toBeVisible();
    await expect.poll(() => page.evaluate(() => document.documentElement.scrollWidth <= window.innerWidth)).toBe(true);
    await page.getByRole("button", { name: "Move back to Recruitment" }).click();
    await page.getByRole("button", { name: "Reopen request" }).click();
    await expect(page.getByText("Pending chair approval", { exact: true })).toBeVisible();
    await page.setViewportSize({ width: 1024, height: 800 });
    await expect(page.getByRole("button", { name: "Go back" })).toBeVisible();
    const layout = await page.evaluate(() => ({
      sidebarRight: document.querySelector(".sidebar")!.getBoundingClientRect().right,
      resultsLeft: document.querySelector(".judge-results")!.getBoundingClientRect().left,
      panelLeft: document.querySelector(".judge-detail")!.getBoundingClientRect().left,
      panelTop: document.querySelector(".judge-detail")!.getBoundingClientRect().top,
      toolbarBottom: document.querySelector(".topbar")!.getBoundingClientRect().bottom,
    }));
    expect(layout.resultsLeft).toBeGreaterThanOrEqual(layout.sidebarRight);
    expect(layout.panelLeft).toBeGreaterThan(layout.resultsLeft);
    expect(layout.panelTop).toBeGreaterThanOrEqual(layout.toolbarBottom);
    await expect(chairPage.locator(".sidebar .brand-symbol")).toHaveAttribute("src", "/brand/atlas-horizontal-compact-light.svg");
    await chairPage.getByRole("button", { name: "Sign out" }).click();
    await expect(chairPage).toHaveURL(/\/login$/);
    await expect(logo).toHaveAttribute("src", logoPath!);
    await expect.poll(() => logo.evaluate((img: HTMLImageElement) => img.naturalWidth)).toBeGreaterThan(0);
  } finally { await chairPage.close(); }
});
