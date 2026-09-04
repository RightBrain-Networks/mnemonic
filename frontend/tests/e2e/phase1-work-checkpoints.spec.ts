import { readFile } from "node:fs/promises";
import { expect, request as playwrightRequest, test, type Locator, type Page } from "@playwright/test";
import { statePath, type E2EState } from "./global.setup";
import { closeDetail, openTab, selectWork, workCard, workPane } from "./surface";

let state: E2EState;

type QueueMotionRecord = {
  title: string;
  keyframes: Array<{ transform: string | null; opacity: string | null }>;
  easing: string;
  duration: number | null;
  startOrder: number;
  finishOrder: number | null;
};

type QueueMotionState = {
  records: QueueMotionRecord[];
  // Every window scroll event and every queue-list scroll event, so a live
  // arrival that nudges either scroller is caught whichever one is active.
  scrollPositions: number[];
  listScrollPositions: number[];
  order: number;
};

// The queue scrolls inside its own list on desktop; below 900px the page scrolls.
type ScrollProbe = {
  scroller: "list" | "document";
  y: number;
  maxScroll: number;
  viewportHeight: number;
  listTop: number;
  listBottom: number;
  windowY: number;
  listY: number;
};

type ExternalWork = {
  work_item: { id: string; version: number };
};

test.beforeAll(async () => {
  state = JSON.parse(await readFile(statePath, "utf8")) as E2EState;
});

type FadeDirection = "in" | "out";

async function sampleFadeMidpoint(
  page: Page,
  targetTitle: string,
  direction: FadeDirection
): Promise<number> {
  const selector = direction === "in"
    ? ".work-list > [data-work-item-id]"
    : "[data-work-item-exit-id]";
  const target = page.locator(selector).filter({ hasText: targetTitle });
  await expect(target).toHaveCount(1);
  await expect.poll(() => target.evaluate((element) => element.getAnimations().some((animation) => {
    const effect = animation.effect;
    return effect instanceof KeyframeEffect
      && effect.getKeyframes().some((frame) => frame.opacity !== undefined);
  })), {
    message: targetTitle + " should have a running opacity animation"
  }).toBe(true);

  return target.evaluate((element, expectedDirection) => {
    const animation = element.getAnimations().find((candidate) => {
      const effect = candidate.effect;
      return effect instanceof KeyframeEffect
        && effect.getKeyframes().some((frame) => frame.opacity !== undefined);
    });
    if (!animation || !(animation.effect instanceof KeyframeEffect)) {
      throw new Error("The expected opacity animation is not running.");
    }
    const frames = animation.effect.getKeyframes();
    const expectedEndpoints = expectedDirection === "in" ? ["0", "1"] : ["1", "0"];
    if (String(frames[0]?.opacity) !== expectedEndpoints[0]
      || String(frames.at(-1)?.opacity) !== expectedEndpoints[1]) {
      throw new Error("The opacity animation has the wrong direction.");
    }
    const duration = animation.effect.getTiming().duration;
    if (typeof duration !== "number") throw new Error("The fade duration is not numeric.");
    animation.pause();
    animation.currentTime = duration / 2;
    const opacity = Number.parseFloat(getComputedStyle(element).opacity);
    void animation.play();
    return opacity;
  }, direction);
}

// The Checkpoints value in the pane's facts strip (cards no longer show a count).
function checkpointFact(pane: Locator): Locator {
  return pane.locator(".detail-facts")
    .getByText("Checkpoints", { exact: true })
    .locator("xpath=following-sibling::dd");
}

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
  const narrowLayout = (page.viewportSize()?.width ?? Number.POSITIVE_INFINITY) <= 900;
  const apiURL = process.env.MNEMONIC_E2E_API_URL;
  const apiKey = process.env.MNEMONIC_E2E_API_KEY;
  if (!apiURL || !apiKey) throw new Error("The disposable E2E API is not configured.");

  const client = await playwrightRequest.newContext({
    baseURL: apiURL,
    extraHTTPHeaders: { Authorization: "Bearer " + apiKey, Accept: "application/json" }
  });

  const createExternalWork = async (workTitle: string, sourceSessionId: string): Promise<ExternalWork> => {
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
    const body = await response.text();
    expect(response.ok(), workTitle + ": " + body).toBe(true);
    return JSON.parse(body) as ExternalWork;
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
        listScrollPositions: [],
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
        const timing = effect instanceof KeyframeEffect ? effect.getTiming() : null;
        const card = this.matches("article.work-item-card")
          ? this
          : this.querySelector("article.work-item-card");
        const record: QueueMotionRecord = {
          title: card?.querySelector(".queue-card-title")?.textContent?.trim() ?? "",
          keyframes: effect instanceof KeyframeEffect
            ? effect.getKeyframes().map((frame) => ({
              transform: typeof frame.transform === "string" ? frame.transform : null,
              opacity: typeof frame.opacity === "string" || typeof frame.opacity === "number"
                ? String(frame.opacity)
                : null
            }))
            : [],
          easing: timing?.easing ?? "",
          duration: typeof timing?.duration === "number" ? timing.duration : null,
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
    await expect(
      page.getByRole("heading", { name: "No matching work records." })
    ).toBeVisible();
    await page.evaluate(() => {
      const motionState = (window as typeof window & {
        __queueMotionTest?: QueueMotionState;
      }).__queueMotionTest;
      if (!motionState) throw new Error("The queue motion observer was not installed.");
      motionState.records.splice(0);
    });
    const emptyWork = await createExternalWork(
      emptyTitle,
      "live-sync-empty-" + state.runId
    );
    const enterMidpoint = await sampleFadeMidpoint(page, emptyTitle, "in");
    expect(enterMidpoint).toBeGreaterThan(0.45);
    expect(enterMidpoint).toBeLessThan(0.55);
    await expect.poll(() => page.evaluate((targetTitle) => {
      const motionState = (window as typeof window & {
        __queueMotionTest?: QueueMotionState;
      }).__queueMotionTest;
      return motionState?.records.some((record) =>
        record.title === targetTitle
        && record.keyframes[0]?.opacity === "0"
        && record.keyframes.at(-1)?.opacity === "1"
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
        && record.keyframes[0]?.opacity === "0"
        && record.keyframes.at(-1)?.opacity === "1"
      ) ?? null;
    }, emptyTitle);
    expect(emptyFade?.duration).toBe(1000);
    expect(emptyFade?.easing.replaceAll(" ", "")).toBe("cubic-bezier(0.83,0,0.17,1)");

    const deletion = await client.post(
      "/api/v1/projects/" + state.projectId + "/work-items/"
        + emptyWork.work_item.id + "/delete",
      { data: { expected_version: emptyWork.work_item.version } }
    );
    expect(deletion.ok(), await deletion.text()).toBe(true);
    const exitMidpoint = await sampleFadeMidpoint(page, emptyTitle, "out");
    expect(exitMidpoint).toBeGreaterThan(0.45);
    expect(exitMidpoint).toBeLessThan(0.55);
    await expect.poll(() => page.evaluate((targetTitle) => {
      const motionState = (window as typeof window & {
        __queueMotionTest?: QueueMotionState;
      }).__queueMotionTest;
      return motionState?.records.some((record) =>
        record.title === targetTitle
        && record.keyframes[0]?.opacity === "1"
        && record.keyframes.at(-1)?.opacity === "0"
        && record.finishOrder !== null
      ) ?? false;
    }, emptyTitle), {
      message: "a result removed from the view should fade out"
    }).toBe(true);
    const emptyExit = await page.evaluate((targetTitle) => {
      const motionState = (window as typeof window & {
        __queueMotionTest?: QueueMotionState;
      }).__queueMotionTest;
      return motionState?.records.find((record) =>
        record.title === targetTitle
        && record.keyframes[0]?.opacity === "1"
        && record.keyframes.at(-1)?.opacity === "0"
      ) ?? null;
    }, emptyTitle);
    expect(emptyExit?.duration).toBe(1000);
    expect(emptyExit?.easing.replaceAll(" ", "")).toBe("cubic-bezier(0.83,0,0.17,1)");
    await expect(
      page.getByRole("heading", { name: "No matching work records." })
    ).toBeVisible();
    await searchbox.fill("");

    const retainedCards = page.locator("article.work-item-card").filter({
      hasText: retainedPrefix
    });
    await expect(retainedCards).toHaveCount(retainedTitles.length, { timeout: 15_000 });
    const retainedHandles = await retainedCards.elementHandles();

    // Scroll partway into the queue through whichever element actually scrolls it:
    // the list's own scrollTop on desktop, the document on the narrow layout.
    const scroll = await page.locator(".work-queue-list").evaluate(async (list): Promise<ScrollProbe> => {
      const motionState = (window as typeof window & {
        __queueMotionTest?: QueueMotionState;
      }).__queueMotionTest;
      if (!motionState) throw new Error("The queue motion observer was not installed.");
      const scrollingElement = document.scrollingElement;
      if (!scrollingElement) throw new Error("The document has no scrolling element.");
      const settle = () => new Promise<void>((resolve) => {
        requestAnimationFrame(() => requestAnimationFrame(() => resolve()));
      });
      list.addEventListener("scroll", () => {
        motionState.listScrollPositions.push(list.scrollTop);
      }, { passive: true });
      document.documentElement.style.scrollBehavior = "auto";
      const listScrolls = getComputedStyle(list).overflowY === "auto"
        && list.scrollHeight > list.clientHeight;
      if (listScrolls) {
        const maxScroll = list.scrollHeight - list.clientHeight;
        list.scrollTop = Math.max(
          1,
          Math.min(list.clientHeight / 2, maxScroll - list.clientHeight)
        );
        await settle();
        return {
          scroller: "list",
          y: list.scrollTop,
          maxScroll,
          viewportHeight: list.clientHeight,
          listTop: 0,
          listBottom: list.scrollHeight,
          windowY: window.scrollY,
          listY: list.scrollTop
        };
      }
      const listRect = list.getBoundingClientRect();
      const listTop = listRect.top + window.scrollY;
      const listBottom = listTop + listRect.height;
      const maxScroll = scrollingElement.scrollHeight - window.innerHeight;
      scrollingElement.scrollTop = Math.max(
        listTop + 1,
        Math.min(listTop + window.innerHeight / 2, maxScroll - window.innerHeight)
      );
      await settle();
      return {
        scroller: "document",
        y: window.scrollY,
        maxScroll,
        viewportHeight: window.innerHeight,
        listTop,
        listBottom,
        windowY: window.scrollY,
        listY: list.scrollTop
      };
    });
    expect(scroll.scroller).toBe(narrowLayout ? "document" : "list");
    expect(scroll.maxScroll).toBeGreaterThan(0);
    expect(scroll.y).toBeGreaterThan(scroll.listTop);
    expect(scroll.y).toBeLessThan(scroll.listBottom);
    expect(scroll.maxScroll - scroll.y).toBeGreaterThanOrEqual(scroll.viewportHeight);
    if (scroll.scroller === "list") expect(scroll.windowY).toBe(0);

    await page.evaluate(() => {
      const motionState = (window as typeof window & {
        __queueMotionTest?: QueueMotionState;
      }).__queueMotionTest;
      if (!motionState) throw new Error("The queue motion observer was not installed.");
      motionState.records.splice(0);
      motionState.scrollPositions.splice(0);
      motionState.listScrollPositions.splice(0);
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
      const list = document.querySelector(".work-queue-list");
      if (!list) throw new Error("The work queue list is not rendered.");
      return {
        records: motionState.records,
        scrollPositions: motionState.scrollPositions,
        listScrollPositions: motionState.listScrollPositions,
        scrollY: window.scrollY,
        listScrollTop: list.scrollTop
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
      expect(fade.duration).toBe(1000);
      expect(fade.easing.replaceAll(" ", "")).toBe(
        "cubic-bezier(0.83,0,0.17,1)"
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

    expect(observed.scrollY).toBe(scroll.windowY);
    expect(observed.listScrollTop).toBe(scroll.listY);
    expect(observed.scrollPositions.every((position) => position === scroll.windowY)).toBe(true);
    expect(observed.listScrollPositions.every((position) => position === scroll.listY)).toBe(true);

    await page.emulateMedia({ reducedMotion: "reduce" });
    await page.evaluate(() => {
      const motionState = (window as typeof window & {
        __queueMotionTest?: QueueMotionState;
      }).__queueMotionTest;
      if (!motionState) throw new Error("The queue motion observer was not installed.");
      motionState.records.splice(0);
      motionState.scrollPositions.splice(0);
      motionState.listScrollPositions.splice(0);
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
      const list = document.querySelector(".work-queue-list");
      if (!list) throw new Error("The work queue list is not rendered.");
      return {
        recordCount: motionState.records.length,
        scrollPositions: motionState.scrollPositions,
        listScrollPositions: motionState.listScrollPositions,
        scrollY: window.scrollY,
        listScrollTop: list.scrollTop
      };
    });
    expect(reducedMotion.recordCount).toBe(0);
    expect(reducedMotion.scrollY).toBe(scroll.windowY);
    expect(reducedMotion.listScrollTop).toBe(scroll.listY);
    expect(reducedMotion.scrollPositions.every((position) => position === scroll.windowY)).toBe(true);
    expect(reducedMotion.listScrollPositions.every((position) => position === scroll.listY)).toBe(true);
  } finally {
    await client.dispose();
  }
});

test("the library hero names the selected project in the vendored italic face", async ({ page }) => {
  await page.goto("/");
  const heading = page.getByRole("heading", { name: /^Work library[.:]/ });
  await expect(heading).toBeVisible();
  await page.locator("#project-select").selectOption(state.projectId);
  await expect(heading).toHaveText(`Work library: ${state.projectName}`);

  // The colon inherits the accent the period carried; the project name must not.
  const mark = heading.locator(".heading-mark");
  const subject = heading.locator(".heading-subject");
  await expect(mark).toHaveText(":");
  await expect(subject).toHaveText(state.projectName);
  const color = (locator: Locator) => locator.evaluate((node) => getComputedStyle(node).color);
  expect(await color(mark)).toBe(await color(page.locator(".page-heading .eyebrow")));
  expect(await color(subject)).toBe(await color(heading));

  const painted = await subject.evaluate((node) => {
    const style = getComputedStyle(node);
    return {
      family: style.fontFamily.split(",")[0].replace(/["']/g, "").trim(),
      opacity: style.opacity,
      style: style.fontStyle,
      synthesis: style.fontSynthesisStyle
    };
  });
  expect(painted).toEqual({
    family: "IBM Plex Sans",
    opacity: "0.8",
    style: "italic",
    synthesis: "none"
  });

  // Synthesis is off, so an italic face the browser never fetched would render upright.
  // A "loaded" status proves the vendored file was downloaded to paint this text.
  const italic = await page.evaluate(async () => {
    await document.fonts.ready;
    return [...document.fonts]
      .filter((face) => face.family.replace(/["']/g, "") === "IBM Plex Sans"
        && face.style === "italic")
      .map((face) => face.status);
  });
  expect(italic.length).toBeGreaterThan(0);
  expect(italic).toContain("loaded");
});

test("one work item groups immutable checkpoints through its full dashboard lifecycle", async ({ page }, testInfo) => {
  const suffix = testInfo.project.name.replace("chromium-", "");
  const title = `Grouped work ${suffix} ${state.runId.slice(0, 8)}`;
  const initial = `Initial immutable context for ${suffix}.`;
  const progress = `Progress learned by the ${suffix} session.`;
  const replacement = `Replacement current context from ${suffix}.`;
  const completion = `Completion evidence for ${suffix}.`;

  await page.goto("/");
  await expect(page.getByRole("heading", { name: /^Work library[.:]/ })).toBeVisible();
  await page.locator("#project-select").selectOption(state.projectId);

  await page.locator(".page-heading").getByRole("button", { name: "New work" }).click();
  const createDialog = page.getByRole("dialog", { name: "Create durable work" });
  await createDialog.getByLabel("Title").fill(title);
  await createDialog.getByLabel("Summary").fill("A single durable objective shared across session checkpoints.");
  await createDialog.getByLabel("Priority").fill("17");
  await createDialog.getByLabel("Initial context checkpoint").fill(initial);
  await createDialog.getByRole("button", { name: "Create work and checkpoint" }).click();

  const card = workCard(page, title);
  await expect(card).toHaveCount(1);
  // New work slides into the pane already selected.
  let pane = workPane(page);
  await expect(pane.locator(".detail-title")).toHaveText(title);
  await expect(card).toHaveAttribute("aria-selected", "true");
  await expect(checkpointFact(pane)).toHaveText("1");

  await expect(pane.locator(".prompt-body")).toHaveText(initial);
  let history = await openTab(pane, "History");
  await expect(history.locator("article.checkpoint")).toHaveCount(1);

  let context = await openTab(pane, "Context");
  await context.getByLabel("Checkpoint text").fill(progress);
  await context.getByRole("button", { name: "Add checkpoint" }).click();
  history = await openTab(pane, "History");
  await expect(history.locator("article.checkpoint")).toHaveCount(2);

  context = await openTab(pane, "Context");
  await context.getByLabel("Checkpoint kind").selectOption("context");
  await context.getByLabel("Checkpoint text").fill(replacement);
  await context.getByRole("button", { name: "Add checkpoint" }).click();
  await expect(context.locator(".prompt-body")).toHaveText(replacement);
  history = await openTab(pane, "History");
  await expect(history.locator("article.checkpoint")).toHaveCount(3);
  await expect(history.locator(".checkpoint textarea, .checkpoint input, .checkpoint button")).toHaveCount(0);
  await expect(history.locator("article.checkpoint").filter({ hasText: initial })).toHaveCount(1);
  await expect(history.locator("article.checkpoint").filter({ hasText: progress })).toHaveCount(1);
  await expect(history.locator("article.checkpoint").filter({ hasText: replacement })).toHaveCount(1);
  await expect(checkpointFact(pane)).toHaveText("3");
  await expect(pane.getByRole("tab", { name: /^History/ })).toContainText("3");

  await closeDetail(page);
  await expect(card).toHaveCount(1);
  await page.getByLabel("Search work items").fill(title);
  await expect(card).toHaveCount(1);
  await card.getByRole("button", { name: /Copy recall pointer/ }).click();
  const pointer = await page.evaluate(() => navigator.clipboard.readText());
  expect(pointer).toContain("work_item_id");
  expect(pointer).toContain("recall_work");

  pane = await selectWork(page, title);
  await pane.getByRole("button", { name: "Edit work item" }).click();
  // The label wraps the textarea, so its text includes the current value; match by prefix.
  await pane.locator(".detail-edit").getByLabel("Summary").fill("Updated durable objective; checkpoint history remains unchanged.");
  await pane.getByRole("button", { name: "Save changes" }).click();
  await expect(pane.locator(".detail-summary")).toHaveText("Updated durable objective; checkpoint history remains unchanged.");
  await expect(pane.getByRole("button", { name: "Save changes" })).toHaveCount(0);

  context = await openTab(pane, "Context");
  await context.getByLabel("Checkpoint text").fill(completion);
  await context.getByRole("button", { name: "Complete with summary" }).click();
  await expect(pane.locator(".detail-identity > .status-badge")).toHaveText(/Done/);
  history = await openTab(pane, "History");
  await expect(history.locator("article.checkpoint")).toHaveCount(4);
  await closeDetail(page);

  await page.getByRole("button", { name: "Done", exact: true }).click();
  await expect(card).toHaveCount(1);
  pane = await selectWork(page, title);
  await pane.getByRole("button", { name: "Delete work item" }).click();
  const deleteDialog = page.getByRole("dialog", { name: "Delete this work item?" });
  await deleteDialog.getByRole("button", { name: "Delete work item" }).click();
  await expect(card).toHaveCount(0);
  await expect(page.locator(".work-detail-pane")).not.toHaveClass(/is-open/);
});
