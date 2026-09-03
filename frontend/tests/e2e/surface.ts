import { expect, type Locator, type Page } from "@playwright/test";

// Shared helpers for the two-column work surface. Every spec runs on the desktop
// and narrow projects: on narrow the detail pane is a full-screen sheet that
// covers the queue, so callers close it (closeDetail) before touching cards,
// filters, or heading buttons again.

export type DetailTabName = "Context" | "History" | "Graph" | "Questions" | "Activity";

export function workPane(page: Page): Locator {
  return page.getByRole("region", { name: "Work context" });
}

export function workCard(page: Page, title: string): Locator {
  return page.locator("article.work-item-card").filter({ hasText: title });
}

// Clicks "Back to work queue" when it is visible (the narrow sheet); otherwise a no-op.
export async function closeDetail(page: Page): Promise<void> {
  const back = page.getByRole("button", { name: "Back to work queue" });
  if (!(await back.isVisible())) return;
  await back.click();
  await expect(back).toBeHidden();
}

// Selects the card with this title, waits for the pane to show it, and returns the pane.
export async function selectWork(page: Page, title: string): Promise<Locator> {
  await closeDetail(page);
  await workCard(page, title).click();
  const pane = workPane(page);
  await expect(pane.locator(".detail-title")).toHaveText(title);
  return pane;
}

// Clicks a detail tab and returns its tabpanel (only the selected tab's body is mounted).
export async function openTab(pane: Locator, name: DetailTabName): Promise<Locator> {
  const tab = pane.getByRole("tab", { name: new RegExp(`^${name}`) });
  await tab.click();
  await expect(tab).toHaveAttribute("aria-selected", "true");
  return pane.locator(`#detail-panel-${name.toLowerCase()}`);
}
