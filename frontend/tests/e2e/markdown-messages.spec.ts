import { expect, request, test, type APIRequestContext, type Page } from "@playwright/test";
import { reportForFixture } from "./job-report-fixture";
import { openTab, workPane } from "./surface";
import { auditTextContrast } from "./text-contrast";

async function createFixture(api: APIRequestContext, title: string) {
  const projectResponse = await api.post("/api/v1/projects", {
    data: { name: `Markdown messages ${crypto.randomUUID()}` }
  });
  expect(projectResponse.ok(), await projectResponse.text()).toBe(true);
  const project = await projectResponse.json() as { id: string };
  const workResponse = await api.post(`/api/v1/projects/${project.id}/work-items`, {
    data: {
      title, summary: "Check human-facing Markdown on the dashboard.", status: "pending", priority: 1,
      initial_checkpoint: {
        prompt: "Compare the displayed choices and report the outcome.",
        source_client: "playwright-api", source_session_id: "markdown-messages"
      }
    }
  });
  expect(workResponse.ok(), await workResponse.text()).toBe(true);
  const { work_item: work } = await workResponse.json() as { work_item: { id: string; version: number } };
  return { project, work };
}

async function apiClient() {
  return request.newContext({
    baseURL: process.env.MNEMONIC_E2E_API_URL,
    extraHTTPHeaders: { Authorization: `Bearer ${process.env.MNEMONIC_E2E_API_KEY}` }
  });
}

async function assertNoPageOverflow(page: Page) {
  expect(await page.evaluate(() => document.documentElement.scrollWidth
    <= document.documentElement.clientWidth)).toBe(true);
}

test("work summaries render Markdown and keep embedded HTML and image loads inert", async ({ page }, testInfo) => {
  const api = await apiClient();
  const imageRequests: string[] = [];
  page.on("request", (value) => {
    if (value.url().includes("markdown-resource.invalid")) imageRequests.push(value.url());
  });
  try {
    const title = "Review the **dashboard update**";
    const { project, work } = await createFixture(api, title);
    const summary = '**Ready for review.** The dashboard uses *consistent formatting*. Read the [release notes](https://example.com/review "Notes\\\" onmouseover=alert(1)") and run `npm test`. <script>document.documentElement.dataset.markdownInjected="yes"</script>';
    const fyiItems = [
      "**Decision:** use `Arial` instead of ~~the old font~~.",
      '![Preview](https://markdown-resource.invalid/image.png) <img src="https://markdown-resource.invalid/pixel.png" onerror="document.documentElement.dataset.markdownInjected=\'yes\'">',
      "[Unsafe](jav&#x61;script:alert(1)) remains inert. مرحبًا 🙂"
    ];
    const closeout = await api.post(`/api/v1/projects/${project.id}/work-items/${work.id}/complete`, {
      data: {
        expected_version: work.version, client_operation_id: crypto.randomUUID(),
        checkpoint: { prompt: "Checked the Markdown fixture.", source_client: "playwright-api", source_session_id: "markdown-messages" },
        job_completion_report: { ...await reportForFixture(api, project.id), summary, fyi_items: fyiItems }
      }
    });
    expect(closeout.ok(), await closeout.text()).toBe(true);
    const closed = await closeout.json() as { job_completion_report: { summary: string; fyi_items: string[] } };
    expect(closed.job_completion_report).toMatchObject({ summary, fyi_items: fyiItems });

    await page.emulateMedia({ reducedMotion: "reduce" });
    await page.goto("/summaries");
    await page.locator("#project-select").selectOption(project.id);
    const card = page.getByRole("article", { name: `Report for ${title}`, exact: true });
    await expect(card.locator("h3")).toHaveText(title);
    await expect(card.locator(".human-report-summary strong")).toHaveText("Ready for review.");
    await expect(card.locator(".human-report-summary em")).toHaveText("consistent formatting");
    await expect(card.locator(".human-report-summary code")).toHaveText("npm test");
    const link = card.getByRole("link", { name: "release notes" });
    await expect(link).toHaveAttribute("href", "https://example.com/review");
    await expect(link).not.toHaveAttribute("onmouseover");
    await link.focus();
    await expect(link).toBeFocused();
    await expect(card.locator(".human-report-fyis > li")).toHaveCount(3);
    await expect(card.locator(".human-report-fyis strong")).toHaveText("Decision:");
    await expect(card.locator(".human-report-fyis s")).toHaveText("the old font");
    await expect(card.locator(".markdown-content script, .markdown-content img, [onerror], [onmouseover]")).toHaveCount(0);
    await expect(card.getByRole("link", { name: "Unsafe" })).toHaveCount(0);
    await expect(card.locator(".human-report-summary")).toContainText("<script>");
    await expect(page.locator("html")).not.toHaveAttribute("data-markdown-injected");
    expect(imageRequests).toEqual([]);
    for (const theme of ["Light", "Dark"]) {
      await page.getByText(theme, { exact: true }).click();
      await expect(page.locator("html")).toHaveAttribute("data-theme", theme.toLowerCase());
      await assertNoPageOverflow(page);
      await expect.poll(async () => (await auditTextContrast(page, card.locator(".job-report-content")))
        .filter((failure) => failure.contrast < 4.5)).toEqual([]);
      await card.locator(".job-report-content").screenshot({ path: testInfo.outputPath(`markdown-summaries-${theme.toLowerCase()}.png`), animations: "disabled" });
    }
  } finally {
    await api.dispose();
  }
});

test("Needs Attention renders block Markdown in the queue and the original work context", async ({ page }, testInfo) => {
  const api = await apiClient();
  try {
    const { project, work } = await createFixture(api, "Choose a rollout plan");
    const question = [
      "## Which rollout should we use?", "", "**Recommendation:** stage first, then deploy.", "",
      "1. **Stage first**", "   - Verify readiness", "2. Deploy immediately", "",
      "> The choice affects release timing.", "", "| Option | Delay |", "| --- | ---: |",
      "| Stage | 5 minutes |", "| Deploy | None |", "", "```sh",
      `npm run check -- --target=${"long-target-".repeat(20)}`, "```", "",
      "Read the [rollout notes](https://example.com/rollout).", "",
      '<img src=x onerror="document.documentElement.dataset.markdownInjected=\'yes\'">',
      "[Unsafe](javascript:alert(1))"
    ].join("\n");
    const response = await api.post(`/api/v1/projects/${project.id}/work-items/${work.id}/gates`, {
      data: {
        gate_type: "human", question, requested_by_client: "playwright-api",
        requested_by_session_id: "markdown-messages", client_operation_id: crypto.randomUUID()
      }
    });
    expect(response.status(), await response.text()).toBe(201);
    expect(await response.json()).toMatchObject({ question });
    await page.emulateMedia({ reducedMotion: "reduce" });
    await page.goto("/attention");
    await page.locator("#project-select").selectOption(project.id);
    const card = page.locator(".attention-card").filter({ hasText: "Choose a rollout plan" });
    const content = card.locator(".attention-question");
    await expect(content.getByRole("heading", { name: "Which rollout should we use?", level: 2 })).toBeVisible();
    await expect(content.locator("ol > li")).toHaveCount(2);
    await expect(content.locator("ol ul > li")).toHaveText("Verify readiness");
    await expect(content.locator("blockquote")).toContainText("release timing");
    await expect(content.getByRole("table")).toBeVisible();
    await expect(content.getByRole("cell", { name: "5 minutes" })).toBeVisible();
    await expect(content.locator("pre code")).toHaveClass("language-sh");
    expect(await content.locator("pre").evaluate((element) => element.scrollWidth > element.clientWidth)).toBe(true);
    await expect(content.locator("img, script, iframe, [onerror]")).toHaveCount(0);
    await expect(content.getByRole("link", { name: "Unsafe" })).toHaveCount(0);
    await expect(page.locator("html")).not.toHaveAttribute("data-markdown-injected");
    for (const theme of ["Light", "Dark"]) {
      await page.getByText(theme, { exact: true }).click();
      await expect(page.locator("html")).toHaveAttribute("data-theme", theme.toLowerCase());
      await assertNoPageOverflow(page);
      await expect.poll(async () => (await auditTextContrast(page, content))
        .filter((failure) => failure.contrast < 4.5)).toEqual([]);
      await content.screenshot({ path: testInfo.outputPath(`markdown-attention-${theme.toLowerCase()}.png`), animations: "disabled" });
    }
    await card.getByRole("button", { name: "Open work context" }).click();
    const questions = await openTab(workPane(page), "Questions");
    await expect(questions.locator(".gate-question h2")).toHaveText("Which rollout should we use?");
    await expect(questions.locator(".gate-question table")).toBeVisible();
    await expect(questions.locator(".gate-question img, .gate-question [onerror]")).toHaveCount(0);
    await assertNoPageOverflow(page);
  } finally {
    await api.dispose();
  }
});
