import { readFile } from "node:fs/promises";
import { expect, test } from "@playwright/test";
import { statePath, type E2EState } from "./global.setup";
import { workPane } from "./surface";

let state: E2EState;
const maximum = Number(process.env.MNEMONIC_E2E_WORK_SUMMARY_MAX_CHARS ?? 2048);

test.beforeAll(async () => {
  state = JSON.parse(await readFile(statePath, "utf8")) as E2EState;
});

test("configurable work summary limit preserves Unicode and validates creation and editing", async ({ page }, testInfo) => {
  const title = `Summary limit ${testInfo.project.name} ${state.runId.slice(0, 8)}`;
  await page.goto("/");
  await page.locator("#project-select").selectOption(state.projectId);
  await page.locator(".topbar").getByRole("button", { name: "New work" }).click();
  const dialog = page.getByRole("dialog", { name: "Create durable work" });
  await dialog.getByLabel("Title", { exact: true }).fill(title);
  const summary = dialog.getByRole("textbox", { name: "Summary", exact: true });
  await expect(dialog.getByText(`Up to ${maximum.toLocaleString("en-US")} characters.`, { exact: true })).toBeVisible();
  if (process.env.MNEMONIC_CAPTURE_SUMMARY_LIMIT_SCREENSHOT && testInfo.project.name === "chromium-desktop") {
    await summary.fill("Keep search results compact while allowing longer summaries when needed.");
    await page.screenshot({ path: "../docs/images/work-summary-limit.png", fullPage: true });
  }
  await summary.fill("🧠".repeat(maximum + 1));
  await expect(summary).toHaveValue("🧠".repeat(maximum + 1));
  expect(await summary.evaluate((input: HTMLTextAreaElement) => input.validationMessage))
    .toContain(`maximum of ${maximum} characters`);
  await dialog.getByLabel("Initial context checkpoint").fill("Standalone summary-limit acceptance context.");
  await dialog.getByRole("button", { name: "Create work and checkpoint" }).click();
  await expect(dialog).toBeVisible();
  await summary.fill("🧠".repeat(maximum));
  expect(await summary.evaluate((input: HTMLTextAreaElement) => input.checkValidity())).toBe(true);
  await dialog.getByRole("button", { name: "Create work and checkpoint" }).click();
  await expect(dialog).toBeHidden();
  const pane = workPane(page);
  await expect(pane.locator(".detail-title")).toHaveText(title);
  await expect(pane.locator(".detail-summary")).toHaveText("🧠".repeat(maximum));
  await pane.getByRole("button", { name: "Edit work item" }).click();
  const edited = pane.locator(".detail-edit").getByRole("textbox", { name: "Summary", exact: true });
  await edited.fill("é".repeat(maximum + 1));
  expect(await edited.evaluate((input: HTMLTextAreaElement) => input.checkValidity())).toBe(false);
  await edited.fill("é".repeat(maximum));
  await pane.getByRole("button", { name: "Save changes" }).click();
  await expect(pane.getByRole("button", { name: "Save changes" })).toHaveCount(0);
  await expect(pane.locator(".detail-summary")).toHaveText("é".repeat(maximum));
  await page.reload();
  await expect(pane.locator(".detail-summary")).toHaveText("é".repeat(maximum));
});
