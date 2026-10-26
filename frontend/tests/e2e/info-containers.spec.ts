import { readFile } from "node:fs/promises";
import { expect, test } from "@playwright/test";
import { statePath, type E2EState } from "./global.setup";

const topics = [
  { path: "/knowledge/domains", label: "Knowledge domains help", key: "mnemonic.info.knowledge-domains", bold: "knowledge domain", italic: "decision documents" },
  { path: "/knowledge/decisions", label: "Decision documents help", key: "mnemonic.info.knowledge-decisions", bold: "decision document", italic: "Architecture Decision Record (ADR)" }
];
let state: E2EState;
test.beforeAll(async () => { state = JSON.parse(await readFile(statePath, "utf8")) as E2EState; });

test("info containers render Markdown and persist keyboard expansion and collapse", async ({ page }, testInfo) => {
  for (const topic of topics) {
    await page.goto(`${topic.path}?project=${state.projectId}`);
    const toggle = page.getByRole("button", { name: topic.label, exact: true });
    const content = page.getByRole("region", { name: topic.label, exact: true });
    await expect(toggle).toHaveAttribute("aria-expanded", "false");
    await expect(content).toBeHidden();
    await expect(page.locator(".page-heading p").filter({ has: toggle })).toContainText(topic.path.endsWith("domains")
      ? "Organize decisions, export documents, and set review policies for each knowledge domain."
      : `Explore decisions, their history, and their implementation in the “${state.projectName}” project.`);
    await toggle.focus();
    await page.keyboard.press("Enter");
    await expect(toggle).toHaveAttribute("aria-expanded", "true");
    await expect(content).toBeVisible();
    await expect(content).toHaveAttribute("id", (await toggle.getAttribute("aria-controls"))!);
    await expect(content.locator("strong")).toHaveText(topic.bold);
    await expect(content.locator("em")).toHaveText(topic.italic);
    await expect(content.locator("p")).toHaveCount(2);
    if (topic.path.endsWith("decisions")) await expect(content.locator("code")).toHaveText(["mnemonic", "mnemonic"]);
    await expect.poll(() => page.evaluate((key) => localStorage.getItem(key), topic.key)).toBe("open");
    await expect.poll(() => page.evaluate(() => document.documentElement.scrollWidth <= innerWidth)).toBe(true);
    const screenshot = testInfo.outputPath(`${topic.path.split("/").pop()}-help.png`);
    await page.screenshot({ path: screenshot, fullPage: true, animations: "disabled" });
    await testInfo.attach(topic.label, { path: screenshot, contentType: "image/png" });
    await page.reload();
    await expect(toggle).toHaveAttribute("aria-expanded", "true");
    await expect(content).toBeVisible();
    await toggle.focus();
    await page.keyboard.press("Space");
    await expect(toggle).toBeFocused();
    await expect(content).toBeHidden();
    await expect.poll(() => page.evaluate((key) => localStorage.getItem(key), topic.key)).toBe("closed");
    await page.reload();
    await expect(toggle).toHaveAttribute("aria-expanded", "false");
    await expect(content).toBeHidden();
  }
});

test("info containers keep independent preferences through client navigation", async ({ page }) => {
  await page.goto(`/knowledge/domains?project=${state.projectId}`);
  await page.getByRole("button", { name: topics[0].label, exact: true }).click();
  const navigation = page.getByRole("navigation", { name: "Workspace navigation" });
  await navigation.getByRole("link", { name: "Decision documents", exact: true }).click();
  await expect(page.getByRole("button", { name: topics[1].label, exact: true })).toHaveAttribute("aria-expanded", "false");
  await navigation.getByRole("link", { name: "Manage domains", exact: true }).click();
  await expect(page.getByRole("button", { name: topics[0].label, exact: true })).toHaveAttribute("aria-expanded", "true");
  await page.getByRole("button", { name: topics[0].label, exact: true }).click();
  await navigation.getByRole("link", { name: "Decision documents", exact: true }).click();
  await page.getByRole("button", { name: topics[1].label, exact: true }).click();
  await navigation.getByRole("link", { name: "Manage domains", exact: true }).click();
  await expect(page.getByRole("button", { name: topics[0].label, exact: true })).toHaveAttribute("aria-expanded", "false");
  await navigation.getByRole("link", { name: "Decision documents", exact: true }).click();
  await expect(page.getByRole("button", { name: topics[1].label, exact: true })).toHaveAttribute("aria-expanded", "true");
});

test("info containers remain usable when localStorage is unavailable", async ({ page }) => {
  await page.addInitScript(() => {
    Object.defineProperty(window, "localStorage", { get() { throw new DOMException("Storage blocked", "SecurityError"); } });
  });
  await page.goto(`/knowledge/domains?project=${state.projectId}`);
  const toggle = page.getByRole("button", { name: topics[0].label, exact: true });
  const content = page.getByRole("region", { name: topics[0].label, exact: true });
  await expect(toggle).toHaveAttribute("aria-expanded", "false");
  await toggle.click();
  await expect(content).toBeVisible();
  await toggle.click();
  await expect(content).toBeHidden();
});
