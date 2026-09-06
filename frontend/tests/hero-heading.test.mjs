import assert from "node:assert/strict";
import { createHash } from "node:crypto";
import { readFile, stat } from "node:fs/promises";
import test from "node:test";

// The hero names the selected project beside the view title. Three files have to agree:
// the dashboard supplies the name, the chrome renders it, and the stylesheet paints it
// with a vendored italic face that no browser is allowed to synthesize.
const CHROME_URL = new URL("../components/dashboard-view-chrome.tsx", import.meta.url);
const DASHBOARD_URL = new URL("../components/dashboard.tsx", import.meta.url);
const CSS_URL = new URL("../app/globals.css", import.meta.url);
const FONT_URL = (file) => new URL(`../public/fonts/${file}`, import.meta.url);

const chrome = await readFile(CHROME_URL, "utf8");
const dashboard = await readFile(DASHBOARD_URL, "utf8");
const css = await readFile(CSS_URL, "utf8");

const heading = chrome.match(/^\s*(<h1>[\s\S]*?<\/h1>)$/m)?.[1] ?? "";

// Selectors are anchored to the line start so a shorthand rule never resolves to the
// longer one that shares its tail.
function ruleBodies(selector) {
  const escaped = selector.replace(/[.*+?^${}()|[\]\\]/g, "\\$&");
  const bodies = [...css.matchAll(new RegExp(`(?:^|\\n)${escaped} \\{([^}]*)\\}`, "g"))];
  assert.ok(bodies.length, `globals.css is missing the ${selector} rule`);
  return bodies.map(([, body]) => body);
}

function declaration(selector, property) {
  for (const body of ruleBodies(selector)) {
    const found = body.match(new RegExp(`(?:^|;)\\s*${property}: ([^;]+)`));
    if (found) return found[1].trim();
  }
  return assert.fail(`globals.css declares no ${property} on ${selector}`);
}

function fontFaces(family) {
  return [...css.matchAll(/@font-face \{([^}]*)\}/g)]
    .map(([, body]) => Object.fromEntries([...body.matchAll(/([\w-]+): ([^;]+);/g)]
      .map(([, name, value]) => [name, value.trim()])))
    .filter((face) => face["font-family"] === `"${family}"`)
    .map((face) => ({ ...face, file: face.src.match(/url\("\/fonts\/([^"]+)"\)/)[1] }));
}

const plex = fontFaces("IBM Plex Sans");
const roman = plex.filter((face) => face["font-style"] === "normal");
const italic = plex.filter((face) => face["font-style"] === "italic");

test("the library hero names the selected project and moves its description inline", () => {
  // Only the library passes a subject, so "Project settings." and "Needs Attention."
  // are unchanged by this heading.
  const subjects = [...dashboard.matchAll(/^\s*subject=\{([^}]*)\}$/gm)].map(([, value]) => value);
  assert.deepEqual(subjects, ["project?.name"]);
  const libraryChrome = dashboard.match(
    /<DashboardViewChrome\n\s*title="Work library"[\s\S]*?\n\s*\/>/
  )?.[0] ?? "";
  assert.match(libraryChrome,
    /subject=\{project\?\.name\}\n\s*subjectDescription=\{project\?\.description \|\|/);
  assert.doesNotMatch(libraryChrome, /\n\s*description=/);
  assert.doesNotMatch(dashboard, /eyebrow="DURABLE WORK FOR TEMPORARY SESSIONS"/);
  assert.equal((chrome.match(/subject\?: string;/g) ?? []).length, 1);
  assert.equal((chrome.match(/subjectDescription\?: string;/g) ?? []).length, 1);
  assert.equal((chrome.match(/description\?: string;/g) ?? []).length, 1);
  assert.match(chrome, /\{description && <p>\{description\}<\/p>\}/);
});

test("the description follows the project name after an em dash", () => {
  assert.match(heading,
    /\{title\}<span className="heading-mark">\{subject \? ":" : "\."\}<\/span>/);
  const nameIndex = heading.indexOf('<span className="heading-subject-name">{subject}</span>');
  const separatorIndex = heading.indexOf(
    '<span className="heading-subject-separator">—</span>'
  );
  const descriptionIndex = heading.indexOf(
    '<span className="heading-subject-description">{subjectDescription}</span>'
  );
  assert.ok(
    nameIndex >= 0 && nameIndex < separatorIndex && separatorIndex < descriptionIndex
  );
});

test("the colon keeps the period's accent and the project name does not take it", () => {
  // Both themes colored a bare child span; the subject is a child span too, so every
  // accent rule has to name the mark explicitly or the project name turns orange.
  assert.ok(!/\.page-heading h1 > span/.test(css), "an accent rule still matches any child span");
  assert.equal(declaration(".page-heading h1 > .heading-mark", "color"), "var(--accent)");
  assert.equal(declaration(".small-mark, .eyebrow, .page-heading h1 > .heading-mark", "color"),
    "var(--accent)");
  assert.match(css,
    /html\[data-theme="dark"\] :is\(\.small-mark, \.eyebrow, \.page-heading h1 > \.heading-mark\)/);
  for (const body of ruleBodies(".heading-subject-name")) {
    assert.ok(!/color:/.test(body), "the project name should inherit the heading's ink");
  }
});

test("the project name is set smaller than the view title it follows", () => {
  // Relative to the heading, so it tracks the fluid clamp and both breakpoint sizes.
  assert.equal(declaration(".page-heading h1 > .heading-subject", "font-size"), ".6em");
  // The heading tracks in absolute pixels tuned for its own size; inherited unchanged
  // that is -.076em of the smaller name. This restates the same ratio relatively.
  assert.equal(declaration(".page-heading h1 > .heading-subject", "letter-spacing"),
    "-.045em");
});

test("the project name is italic at 80% opacity and never a synthesized slant", () => {
  assert.equal(declaration(".page-heading h1 > .heading-subject", "font-style"), "italic");
  assert.equal(declaration(".heading-subject-name", "opacity"), ".8");
  // Without this a missing italic face would silently render as a faux oblique.
  assert.equal(declaration(".page-heading h1 > .heading-subject", "font-synthesis-style"), "none");
  // A long project name would otherwise widen the hero past the viewport.
  assert.equal(declaration(".page-heading h1 > .heading-subject", "overflow-wrap"), "anywhere");
});

test("the project description shares the name size and is painted at 50% opacity", () => {
  assert.equal(declaration(".heading-subject-separator", "margin-inline"), ".45em");
  assert.equal(declaration(".heading-subject-description", "opacity"), ".5");
  for (const body of ruleBodies(".heading-subject-description")) {
    assert.ok(!/font-size:/.test(body), "the description should inherit the project name size");
  }
});

test("the heading font ships a real italic covering the same subsets as its roman", () => {
  assert.equal(css.match(/--display: ([^;]+);/)[1].split(",")[0].trim(), '"IBM Plex Sans"');
  assert.equal(italic.length, roman.length);
  // Same subset split and weight axis, so italic text never falls back mid-string.
  assert.deepEqual(italic.map((face) => face["unicode-range"]),
    roman.map((face) => face["unicode-range"]));
  assert.deepEqual(italic.map((face) => face["font-weight"]),
    roman.map((face) => face["font-weight"]));
  assert.deepEqual(italic.map((face) => face.file), [
    "ibm-plex-sans-latin-ext-italic-variable.woff2",
    "ibm-plex-sans-latin-italic-variable.woff2"
  ]);
});

test("every declared italic subset is vendored and is its own file", async () => {
  const digests = new Map();
  for (const face of plex) {
    const file = FONT_URL(face.file);
    assert.ok((await stat(file)).isFile(), `${face.file} is declared but not vendored`);
    const contents = await readFile(file);
    // WOFF2 signature: a truncated or HTML-error download would not carry it.
    assert.equal(contents.subarray(0, 4).toString("latin1"), "wOF2", `${face.file} is not WOFF2`);
    digests.set(createHash("sha256").update(contents).digest("hex"), face.file);
  }
  // A roman file copied under an italic name would declare italic and render upright.
  assert.equal(digests.size, plex.length, "two IBM Plex Sans faces share the same bytes");
});
