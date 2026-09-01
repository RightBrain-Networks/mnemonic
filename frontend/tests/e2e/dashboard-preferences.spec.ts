import { readFile } from "node:fs/promises";
import { expect, test } from "@playwright/test";
import { statePath, type E2EState } from "./global.setup";

let state: E2EState;

test.beforeAll(async () => {
  state = JSON.parse(await readFile(statePath, "utf8")) as E2EState;
});

test("project, filter, and sort selections survive a reload", async ({ page }) => {
  await page.goto("/");
  const projectSelect = page.locator("#project-select");
  await projectSelect.selectOption(state.projectId);

  const activeFilter = page.getByRole("button", { name: "Active", exact: true });
  await activeFilter.click();
  await page.getByText("Priority", { exact: true }).click();

  await expect(activeFilter).toHaveAttribute("aria-pressed", "true");
  await expect(page.getByRole("radio", { name: "Priority" })).toBeChecked();
  await expect.poll(() => page.evaluate(() => ({
    project: localStorage.getItem("mnemonic.project"),
    status: localStorage.getItem("mnemonic.status"),
    sort: localStorage.getItem("mnemonic.sort")
  }))).toEqual({
    project: state.projectId,
    status: "active",
    sort: "priority"
  });

  await page.reload();

  await expect(projectSelect).toHaveValue(state.projectId);
  await expect(activeFilter).toHaveAttribute("aria-pressed", "true");
  await expect(page.getByRole("radio", { name: "Priority" })).toBeChecked();
});
