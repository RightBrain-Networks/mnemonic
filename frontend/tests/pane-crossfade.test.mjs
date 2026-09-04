import assert from "node:assert/strict";
import { readFile } from "node:fs/promises";
import test from "node:test";
import { PANE_VIEW_TRANSITION_NAMES, paneCrossfadeTargets } from "../lib/pane-crossfade.ts";

// The transition lives entirely in the stylesheet: usePaneCrossfade only renames the panes
// it wants captured, so these rules are the whole of its timing.
const CSS_URL = new URL("../app/globals.css", import.meta.url);
const css = await readFile(CSS_URL, "utf8");

const paneNames = Object.values(PANE_VIEW_TRANSITION_NAMES);
const selectors = {
  variables: ":root",
  groups: paneNames.map((name) => `::view-transition-group(${name})`).join(", "),
  outgoing: paneNames.map((name) => `::view-transition-old(${name})`).join(", "),
  incoming: paneNames.map((name) => `::view-transition-new(${name})`).join(", "),
  heldRoot: ["old", "new"]
    .map((half) => `html[data-pane-crossfade]::view-transition-${half}(root)`).join(",\n"),
  themedRoot: ["old", "new"].map((half) => `::view-transition-${half}(root)`).join(",\n")
};

// Selectors are anchored to the start of a line so the bare root rule never resolves to the
// scoped one that shares its tail.
function ruleBodies(selector) {
  const escaped = selector.replace(/[.*+?^${}()|[\]\\]/g, "\\$&");
  const bodies = [...css.matchAll(new RegExp(`(?:^|\\n)${escaped} \\{([^}]*)\\}`, "g"))];
  assert.ok(bodies.length, `globals.css is missing the ${selector} rule`);
  return bodies.map(([, body]) => body);
}

function declaration(selector, property) {
  for (const body of ruleBodies(selector)) {
    const found = body.match(new RegExp(`${property}: ([^;]+);`));
    if (found) return found[1].trim();
  }
  return assert.fail(`globals.css declares no ${property} on ${selector}`);
}

test("the panes cross-dissolve over one adjustable duration", () => {
  // A pane easing on its own value could finish while the other was still moving.
  assert.equal(declaration(selectors.variables, "--pane-crossfade-duration"), "400ms");
  for (const half of [selectors.groups, selectors.outgoing, selectors.incoming]) {
    assert.equal(declaration(half, "animation-duration"), "var(--pane-crossfade-duration)");
  }
});

test("the incoming half eases in and the outgoing half eases out on the circ curves", () => {
  // easings.net easeInCirc and easeOutCirc.
  assert.equal(declaration(selectors.variables, "--pane-crossfade-ease-in"),
    "cubic-bezier(0.55, 0, 1, 0.45)");
  assert.equal(declaration(selectors.variables, "--pane-crossfade-ease-out"),
    "cubic-bezier(0, 0.55, 0.45, 1)");
  assert.equal(declaration(selectors.incoming, "animation-timing-function"),
    "var(--pane-crossfade-ease-in)");
  assert.equal(declaration(selectors.outgoing, "animation-timing-function"),
    "var(--pane-crossfade-ease-out)");
});

test("a filter change holds the rest of the page still without disarming the theme fade", () => {
  // Everything the filter did not rename is captured as the root. Left to the theme's own
  // rule it would fade too, and the filter button would answer the click late.
  assert.equal(declaration(selectors.heldRoot, "animation"), "none");
  assert.equal(declaration(selectors.heldRoot, "mix-blend-mode"), "normal");
  assert.equal(declaration(selectors.themedRoot, "animation-duration"),
    "var(--theme-crossfade-duration)");
});

test("a lifecycle filter cross-dissolves the queue, and the detail pane only when it deselects", () => {
  assert.deepEqual(paneCrossfadeTargets("unchanged"), { queue: false, detail: false });
  assert.deepEqual(paneCrossfadeTargets("refilter"), { queue: true, detail: false });
  assert.deepEqual(paneCrossfadeTargets("refilter-and-deselect"), { queue: true, detail: true });
});

test("clearing filters cross-dissolves the queue even when the lifecycle filter is unchanged", () => {
  assert.deepEqual(paneCrossfadeTargets("unchanged", true), { queue: true, detail: false });
  assert.deepEqual(paneCrossfadeTargets("refilter-and-deselect", true), { queue: true, detail: true });
  // An explicitly unrenamed query still leaves an untouched pane alone.
  assert.deepEqual(paneCrossfadeTargets("refilter", false), { queue: false, detail: false });
});
