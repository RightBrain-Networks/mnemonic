import assert from "node:assert/strict";
import { readFile } from "node:fs/promises";
import test from "node:test";

// The detail placeholder draws the queue's whole keyboard map. Two files have to agree:
// the pane emits the caps in the groups a keyboard has, and the stylesheet seats those
// groups on one center line against a single column of labels.
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
  for (const key of ["up", "left", "down", "right"]) {
    assert.equal(declaration(`.detail-empty .key-${key}`, "grid-area"), key);
  }
});

test("the digit caps are their own group on the cluster's center line", () => {
  assert.match(placeholder, /<span className="key-digits"><kbd>1<\/kbd>–<kbd>0<\/kbd><\/span>/);
  // Two columns of keys and labels; both key groups center in the first one, so the
  // narrower digit pair lines up with the wider arrow cluster rather than its left cap.
  assert.equal(declaration(".detail-empty .detail-empty-keys", "grid-template-columns"),
    "auto auto");
  assert.equal(declaration(".detail-empty .key-cluster, .detail-empty .key-digits",
    "justify-self"), "center");
  // Unlike the arrows, the digit label does not name its keys, so these caps stay read.
  assert.ok(!/key-digits" aria-hidden/.test(placeholder));
});

test("each label names its own directions rather than the row it sits beside", () => {
  // The cluster puts down in the bottom row with left and right, so row position alone
  // would attach the wrong meaning to it.
  assert.match(placeholder,
    /<span className="detail-empty-hint">select work item \(up\/down\)<\/span>/);
  assert.match(placeholder,
    /<span className="detail-empty-hint">cycle states \(left\/right\)<\/span>/);
  assert.match(placeholder, /<span className="detail-empty-hint">select a project<\/span>/);
  // The two arrow labels are one block centered on the cluster, not a line per cap row.
  assert.equal(declaration(".detail-empty .key-legend", "display"), "grid");
  assert.equal(declaration(".detail-empty .detail-empty-keys", "align-items"), "center");
  assert.equal(declaration(".detail-empty .detail-empty-keys .detail-empty-hint", "margin"), "0");
});
