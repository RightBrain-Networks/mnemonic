import { readFile } from "node:fs/promises";
import { expect, request as playwrightRequest, test } from "@playwright/test";
import { statePath, type E2EState } from "./global.setup";

let state: E2EState;

type QueueMotionRecord = {
  title: string;
  keyframes: Array<{ transform: string | null; opacity: string | null }>;
  easing: string;
  startOrder: number;
  finishOrder: number | null;
};

type QueueMotionState = {
  records: QueueMotionRecord[];
  scrollPositions: number[];
  order: number;
};

test.beforeAll(async () => {
  state = JSON.parse(await readFile(statePath, "utf8")) as E2EState;
});

test("external API writes appear through live browser sync", async ({ page }, testInfo) => {
  const testKey = [
    testInfo.project.name,
    state.runId.slice(0, 8),
    "r" + testInfo.retry
  ].join("-");
  const title = "New animated work " + testKey;
  const rapidTitle = "Second animated work " + testKey;
  const animatedTitles = [title, rapidTitle];
  const emptySearchToken = "emptyarrival" + testKey.replaceAll("-", "");
  const emptyTitle = "First empty result " + emptySearchToken;
  const reducedMotionTitle = "New reduced-motion work " + testKey;
  const retainedPrefix = "Existing animated work " + testKey;
  const retainedTitles = Array.from(
    { length: 8 },
    (_, index) => retainedPrefix + " " + (index + 1)
  );
  const apiURL = process.env.MNEMONIC_E2E_API_URL;
  const apiKey = process.env.MNEMONIC_E2E_API_KEY;
  if (!apiURL || !apiKey) throw new Error("The disposable E2E API is not configured.");

  const client = await playwrightRequest.newContext({
    baseURL: apiURL,
    extraHTTPHeaders: { Authorization: "Bearer " + apiKey, Accept: "application/json" }
  });

  const createExternalWork = async (workTitle: string, sourceSessionId: string) => {
    const response = await client.post(
      "/api/v1/projects/" + state.projectId + "/work-items",
      {
        data: {
          title: workTitle,
          summary: "Created outside the dashboard for the live queue animation regression.",
          priority: 5,
          initial_checkpoint: {
            prompt: "This item must arrive over the live invalidation connection.",
            source_client: "playwright-api",
            source_session_id: sourceSessionId,
            tags: [],
            source_metadata: {}
          }
        }
      }
    );
    expect(response.ok(), workTitle + ": " + await response.text()).toBe(true);
  };

  try {
    for (const [index, retainedTitle] of retainedTitles.entries()) {
      await createExternalWork(
        retainedTitle,
        "live-sync-seed-" + state.runId + "-" + index
      );
    }

    await page.addInitScript(() => {
      const motionState: QueueMotionState = {
        records: [],
        scrollPositions: [],
        order: 0
      };
      const testWindow = window as typeof window & {
        __queueMotionTest?: QueueMotionState;
      };
      testWindow.__queueMotionTest = motionState;

      const nativeAnimate = Element.prototype.animate;
      Element.prototype.animate = function (
        this: Element,
        keyframes: Keyframe[] | PropertyIndexedKeyframes | null,
        options?: number | KeyframeAnimationOptions
      ): Animation {
        const animation = nativeAnimate.call(this, keyframes, options);
        const effect = animation.effect;
        const card = this.matches("article.work-item-card")
          ? this
          : this.querySelector("article.work-item-card");
        const record: QueueMotionRecord = {
          title: card?.querySelector(".card-title h2")?.textContent?.trim() ?? "",
          keyframes: effect instanceof KeyframeEffect
            ? effect.getKeyframes().map((frame) => ({
              transform: typeof frame.transform === "string" ? frame.transform : null,
              opacity: typeof frame.opacity === "string" || typeof frame.opacity === "number"
                ? String(frame.opacity)
                : null
            }))
            : [],
          easing: effect instanceof KeyframeEffect ? effect.getTiming().easing ?? "" : "",
          startOrder: ++motionState.order,
          finishOrder: null
        };
        motionState.records.push(record);
        void animation.finished.then(() => {
          record.finishOrder = ++motionState.order;
        }, () => undefined);
        return animation;
      };

      window.addEventListener("scroll", () => {
        motionState.scrollPositions.push(window.scrollY);
      }, { passive: true });
    });

    await page.goto("/");
    await page.locator("#project-select").selectOption(state.projectId);
    await expect(page.locator(".sync-status")).toHaveText("Live updates");
    await expect(page.getByRole("button", { name: "Refresh" })).toBeVisible();

    const searchbox = page.getByRole("searchbox", { name: "Search work items" });
    await searchbox.fill(emptySearchToken);
    await expect(page.getByRole("heading", { name: "No matching work." })).toBeVisible();
    await page.evaluate(() => {
      const motionState = (window as typeof window & {
        __queueMotionTest?: QueueMotionState;
      }).__queueMotionTest;
      if (!motionState) throw new Error("The queue motion observer was not installed.");
      motionState.records.splice(0);
    });
    await createExternalWork(emptyTitle, "live-sync-empty-" + state.runId);
    await expect.poll(() => page.evaluate((targetTitle) => {
      const motionState = (window as typeof window & {
        __queueMotionTest?: QueueMotionState;
      }).__queueMotionTest;
      return motionState?.records.some((record) =>
        record.title === targetTitle
        && record.keyframes.some((frame) => frame.opacity !== null)
        && record.finishOrder !== null
      ) ?? false;
    }, emptyTitle), {
      message: "the first result added to an empty view should fade in"
    }).toBe(true);
    const emptyFade = await page.evaluate((targetTitle) => {
      const motionState = (window as typeof window & {
        __queueMotionTest?: QueueMotionState;
      }).__queueMotionTest;
      return motionState?.records.find((record) =>
        record.title === targetTitle
        && record.keyframes.some((frame) => frame.opacity !== null)
      ) ?? null;
    }, emptyTitle);
    expect(emptyFade?.easing.replaceAll(" ", "")).toBe("cubic-bezier(0.33,1,0.68,1)");
    expect(emptyFade?.keyframes[0]?.opacity).toBe("0");
    expect(emptyFade?.keyframes.at(-1)?.opacity).toBe("1");
    await searchbox.fill("");

    const retainedCards = page.locator("article.work-item-card").filter({
      hasText: retainedPrefix
    });
    await expect(retainedCards).toHaveCount(retainedTitles.length, { timeout: 15_000 });
    const retainedHandles = await retainedCards.elementHandles();

    const scroll = await page.locator(".work-list").evaluate(async (list) => {
      const scrollingElement = document.scrollingElement;
      if (!scrollingElement) throw new Error("The document has no scrolling element.");
      const listTop = list.getBoundingClientRect().top + window.scrollY;
      const listBottom = listTop + list.getBoundingClientRect().height;
      const maxScroll = scrollingElement.scrollHeight - window.innerHeight;
      document.documentElement.style.scrollBehavior = "auto";
      scrollingElement.scrollTop = Math.max(listTop + 1, maxScroll - 50);
      await new Promise<void>((resolve) => {
        requestAnimationFrame(() => requestAnimationFrame(() => resolve()));
      });
      return { y: window.scrollY, listTop, listBottom, maxScroll };
    });
    expect(scroll.maxScroll).toBeGreaterThan(0);
    expect(scroll.y).toBeGreaterThan(scroll.listTop);
    expect(scroll.y).toBeLessThan(scroll.listBottom);

    await page.evaluate(() => {
      const motionState = (window as typeof window & {
        __queueMotionTest?: QueueMotionState;
      }).__queueMotionTest;
      if (!motionState) throw new Error("The queue motion observer was not installed.");
      motionState.records.splice(0);
      motionState.scrollPositions.splice(0);
      motionState.order = 0;
    });

    await createExternalWork(title, "live-sync-" + state.runId);

    await expect.poll(() => page.evaluate(() => {
      const motionState = (window as typeof window & {
        __queueMotionTest?: QueueMotionState;
      }).__queueMotionTest;
      return motionState?.records.some((record) =>
        record.keyframes.some((frame) => frame.transform !== null)
        && record.finishOrder === null
      ) ?? false;
    }), {
      message: "retained cards should still be sliding"
    }).toBe(true);
    const enteringCard = page.locator(".work-list > [data-work-item-id]").filter({
      hasText: title
    });
    await expect(enteringCard).toHaveAttribute("inert", "");
    await expect(enteringCard).toHaveCSS("opacity", "0");

    await page.evaluate(() => {
      for (const animation of document.getAnimations()) {
        const effect = animation.effect;
        if (!(effect instanceof KeyframeEffect)) continue;
        const target = effect.target;
        if (!(target instanceof HTMLElement)
          || !target.matches(".work-list > [data-work-item-id]")) continue;
        if (effect.getKeyframes().some((frame) => typeof frame.transform === "string")) {
          animation.pause();
        }
      }
    });
    await createExternalWork(rapidTitle, "live-sync-rapid-" + state.runId);
    const rapidEnteringCard = page.locator(".work-list > [data-work-item-id]").filter({
      hasText: rapidTitle
    });
    await expect(rapidEnteringCard).toHaveAttribute("inert", "");
    await expect(rapidEnteringCard).toHaveCSS("opacity", "0");
    await expect(enteringCard).toHaveAttribute("inert", "");
    await expect(enteringCard).toHaveCSS("opacity", "0");

    await page.getByRole("button", { name: "Refresh" }).evaluate((button) => {
      (button as HTMLButtonElement).click();
    });

    await expect.poll(() => page.evaluate((targetTitles) => {
      const motionState = (window as typeof window & {
        __queueMotionTest?: QueueMotionState;
      }).__queueMotionTest;
      return targetTitles.every((targetTitle) => motionState?.records.some((record) =>
          record.title === targetTitle
          && record.keyframes.some((frame) => frame.opacity !== null)
          && record.finishOrder !== null
        ) ?? false);
    }, animatedTitles), {
      message: "rapidly arriving cards should finish fading after the latest bounce"
    }).toBe(true);

    const card = page.locator("article.work-item-card").filter({ hasText: title });
    const rapidCard = page.locator("article.work-item-card").filter({ hasText: rapidTitle });
    await expect(card).toHaveCount(1);
    await expect(card).toBeVisible();
    await expect(rapidCard).toHaveCount(1);
    await expect(rapidCard).toBeVisible();
    await expect(enteringCard).not.toHaveAttribute("inert", "");
    await expect(rapidEnteringCard).not.toHaveAttribute("inert", "");
    await expect(retainedCards).toHaveCount(retainedTitles.length);
    expect(await Promise.all(retainedHandles.map((handle) =>
      handle.evaluate((element) => element.isConnected)
    ))).toEqual(retainedTitles.map(() => true));

    const observed = await page.evaluate(() => {
      const motionState = (window as typeof window & {
        __queueMotionTest?: QueueMotionState;
      }).__queueMotionTest;
      if (!motionState) throw new Error("The queue motion observer was not installed.");
      return {
        records: motionState.records,
        scrollPositions: motionState.scrollPositions,
        scrollY: window.scrollY
      };
    });
    const targetFades = animatedTitles.map((targetTitle) => {
      const fades = observed.records.filter((record) =>
        record.title === targetTitle
        && record.keyframes.some((frame) => frame.opacity !== null)
      );
      expect(fades.length, targetTitle + " should fade").toBeGreaterThan(0);
      const fade = fades.reduce((first, record) =>
        record.startOrder < first.startOrder ? record : first
      );
      expect(fade.easing.replaceAll(" ", "")).toBe(
        "cubic-bezier(0.33,1,0.68,1)"
      );
      expect(fade.keyframes[0]?.opacity).toBe("0");
      expect(fade.keyframes.at(-1)?.opacity).toBe("1");
      expect(fade.finishOrder).not.toBeNull();
      return fade;
    });
    const firstFadeOrder = Math.min(...targetFades.map((fade) => fade.startOrder));

    for (const retainedTitle of retainedTitles) {
      const retainedAnimation = observed.records.find((record) =>
        record.title === retainedTitle
        && record.keyframes.some((frame) => frame.transform !== null)
        && record.finishOrder !== null
        && record.finishOrder < firstFadeOrder
      );
      expect(retainedAnimation, retainedTitle + " should slide before the fade").toBeDefined();
      expect(retainedAnimation!.easing).toBe("linear");
      expect(retainedAnimation!.keyframes.length).toBeGreaterThan(2);
    }

    expect(observed.scrollY).toBe(scroll.y);
    expect(observed.scrollPositions.every((position) => position === scroll.y)).toBe(true);

    await page.emulateMedia({ reducedMotion: "reduce" });
    await page.evaluate(() => {
      const motionState = (window as typeof window & {
        __queueMotionTest?: QueueMotionState;
      }).__queueMotionTest;
      if (!motionState) throw new Error("The queue motion observer was not installed.");
      motionState.records.splice(0);
      motionState.scrollPositions.splice(0);
    });
    await createExternalWork(reducedMotionTitle, "live-sync-reduced-" + state.runId);
    await expect(page.locator("article.work-item-card").filter({
      hasText: reducedMotionTitle
    })).toBeVisible();
    const reducedMotion = await page.evaluate(() => {
      const motionState = (window as typeof window & {
        __queueMotionTest?: QueueMotionState;
      }).__queueMotionTest;
      if (!motionState) throw new Error("The queue motion observer was not installed.");
      return {
        recordCount: motionState.records.length,
        scrollPositions: motionState.scrollPositions,
        scrollY: window.scrollY
      };
    });
    expect(reducedMotion.recordCount).toBe(0);
    expect(reducedMotion.scrollY).toBe(scroll.y);
    expect(reducedMotion.scrollPositions.every((position) => position === scroll.y)).toBe(true);
  } finally {
    await client.dispose();
  }
});

test("one work item groups immutable checkpoints through its full dashboard lifecycle", async ({ page }, testInfo) => {
  const suffix = testInfo.project.name.replace("chromium-", "");
  const title = `Grouped work ${suffix} ${state.runId.slice(0, 8)}`;
  const initial = `Initial immutable context for ${suffix}.`;
  const progress = `Progress learned by the ${suffix} session.`;
  const replacement = `Replacement current context from ${suffix}.`;
  const completion = `Completion evidence for ${suffix}.`;

  await page.goto("/");
  await expect(page.getByRole("heading", { name: "Work library." })).toBeVisible();
  await page.locator("#project-select").selectOption(state.projectId);

  await page.locator(".page-heading").getByRole("button", { name: "New work" }).click();
  const createDialog = page.getByRole("dialog", { name: "Create durable work" });
  await createDialog.getByLabel("Title").fill(title);
  await createDialog.getByLabel("Summary").fill("A single durable objective shared across session checkpoints.");
  await createDialog.getByLabel("Priority").fill("17");
  await createDialog.getByLabel("Initial context checkpoint").fill(initial);
  await createDialog.getByRole("button", { name: "Create work and checkpoint" }).click();

  const card = page.locator("article.work-item-card").filter({ hasText: title });
  await expect(card).toHaveCount(1);
  await expect(card).toContainText("1 checkpoint");
  await card.getByRole("button", { name: title, exact: true }).click();

  let detail = page.getByRole("dialog", { name: "Work context" });
  await expect(detail.locator(".prompt-body")).toHaveText(initial);
  await expect(detail.locator("article.checkpoint")).toHaveCount(1);

  await detail.getByLabel("Checkpoint text").fill(progress);
  await detail.getByRole("button", { name: "Add checkpoint" }).click();
  await expect(detail.locator("article.checkpoint")).toHaveCount(2);

  await detail.getByLabel("Checkpoint kind").selectOption("context");
  await detail.getByLabel("Checkpoint text").fill(replacement);
  await detail.getByRole("button", { name: "Add checkpoint" }).click();
  await expect(detail.locator(".prompt-body")).toHaveText(replacement);
  await expect(detail.locator("article.checkpoint")).toHaveCount(3);
  await expect(detail.locator(".checkpoint textarea, .checkpoint input, .checkpoint button")).toHaveCount(0);
  await expect(detail.locator("article.checkpoint").filter({ hasText: initial })).toHaveCount(1);
  await expect(detail.locator("article.checkpoint").filter({ hasText: progress })).toHaveCount(1);
  await expect(detail.locator("article.checkpoint").filter({ hasText: replacement })).toHaveCount(1);

  await detail.getByRole("button", { name: "Close dialog" }).click();
  await expect(card).toHaveCount(1);
  await expect(card).toContainText("3 checkpoints");

  await page.getByLabel("Search work items").fill(title);
  await expect(card).toHaveCount(1);
  await card.getByRole("button", { name: "Copy recall pointer" }).click();
  const pointer = await page.evaluate(() => navigator.clipboard.readText());
  expect(pointer).toContain("work_item_id");
  expect(pointer).toContain("recall_work");

  await card.getByRole("button", { name: title, exact: true }).click();
  detail = page.getByRole("dialog", { name: "Work context" });
  await detail.getByRole("button", { name: "Edit work item" }).click();
  const editor = page.getByRole("dialog", { name: "Edit work item" });
  await editor.getByLabel("Summary").fill("Updated durable objective; checkpoint history remains unchanged.");
  await editor.getByRole("button", { name: "Save changes" }).click();
  detail = page.getByRole("dialog", { name: "Work context" });
  await expect(detail).toContainText("Updated durable objective; checkpoint history remains unchanged.");

  await detail.getByLabel("Checkpoint text").fill(completion);
  await detail.getByRole("button", { name: "Complete with summary" }).click();
  await expect(detail.locator(".status-badge")).toHaveText(/Done/);
  await expect(detail.locator("article.checkpoint")).toHaveCount(4);
  await detail.getByRole("button", { name: "Close dialog" }).click();

  await page.getByRole("button", { name: "Done", exact: true }).click();
  await expect(card).toHaveCount(1);
  await card.getByRole("button", { name: `Delete ${title}` }).click();
  const deleteDialog = page.getByRole("dialog", { name: "Delete this work item?" });
  await deleteDialog.getByRole("button", { name: "Delete work item" }).click();
  await expect(card).toHaveCount(0);
});
