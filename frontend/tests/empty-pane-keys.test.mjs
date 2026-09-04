import assert from "node:assert/strict";
import { readFile } from "node:fs/promises";
import test from "node:test";

// The detail placeholder draws the queue's whole keyboard map. Two files have to agree:
// the pane emits one arrow cluster, and the stylesheet lays it out in the inverted T
// those four keys sit in on a real keyboard.
const PANE_URL = new URL("../components/work-detail-pane.tsx", import.meta.url);
const CSS_URL = new URL("../app/globals.css", import.meta.url);

const pane = await readFile(PANE_URL, "utf8");
const css = await readFile(CSS_URL, "utf8");
const placeholder = pane.match(/function EmptyPane\(\) \{([\s\S]*?)\n\}/)[1];

// Selectors are anchored to the line start so a shorthand rule never resolves to the
// longer one that shares its tail.
function ruleBody(selector) {
  const escaped = selector.replace(/[.*+?^${}()|[\]\\]/g, "\\$&");
  const found = css.match(new RegExp(`(?:^|\\n)${escaped} \\{([^}]*)\\}`));
  assert.ok(found, `globals.css is missing the ${selector} rule`);
  return found[1];
}

function declaration(selector, property) {
  const found = ruleBody(selector).match(new RegExp(`(?:^|;)\\s*${property}: ([^;]+)`));
  return found ? found[1].trim() : assert.fail(`globals.css declares no ${property} on ${selector}`);
}

test("the four arrows render once, as one cluster the reader is not asked to read", () => {
  assert.match(placeholder, new RegExp(
    '<span className="key-cluster" aria-hidden="true">\\s*'
    + '<kbd className="key-up">↑</kbd><kbd className="key-left">←</kbd>'
    + '<kbd className="key-down">↓</kbd><kbd className="key-right">→</kbd>'
  ));
  // One cap per arrow: a second copy of the pair would drift from the cluster.
  for (const arrow of ["↑", "←", "↓", "→"]) {
    assert.equal((placeholder.match(new RegExp(`<kbd className="key-\\w+">${arrow}</kbd>`, "g")) ?? []).length, 1);
  }
});

test("the stylesheet lays those caps out in a keyboard's inverted T", () => {
  assert.equal(declaration(".detail-empty .key-cluster", "grid-template-areas"),
    '". up ." "left down right"');
  // Equal fixed columns, so up stays centered over down instead of over the label.
  assert.equal(declaration(".detail-empty .key-cluster", "grid-template-columns"), "repeat(3, 22px)");
  for (const [key, area] of [["up", "up"], ["left", "left"], ["down", "down"], ["right", "right"]]) {
    assert.equal(declaration(`.detail-empty .key-${key}`, "grid-area"), area);
  }
});

test("each label names its own pair rather than the cluster row beside it", () => {
  // Down sits in the bottom row next to "cycle states", so the pairs carry the meaning.
  assert.match(placeholder, /<span className="key-pair">↑↓<\/span>move the selection/);
  assert.match(placeholder, /<span className="key-pair">←→<\/span>cycle states/);
});

test("the digit hint still keeps its own caps below the cluster", () => {
  assert.match(placeholder,
    /<p className="detail-empty-hint"><kbd>1<\/kbd>–<kbd>0<\/kbd>select a project<\/p>/);
  assert.equal(declaration(".detail-empty .detail-empty-keys + .detail-empty-hint", "margin-top"),
    "12px");
});
