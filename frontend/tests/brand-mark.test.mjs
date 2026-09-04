import assert from "node:assert/strict";
import { readFile } from "node:fs/promises";
import test from "node:test";

// The brand artwork exists three times: the source asset, the favicon Next.js
// serves from app/icon.svg, and the sidebar mark inlined into dashboard.tsx.
// Nothing but these assertions keeps the copies from drifting apart.
const SOURCE_URL = new URL("../../images/mnemonic_logo.svg", import.meta.url);
const FAVICON_URL = new URL("../app/icon.svg", import.meta.url);
const DASHBOARD_URL = new URL("../components/dashboard.tsx", import.meta.url);

const JSX_ATTRIBUTE_NAMES = {
  strokeWidth: "stroke-width",
  strokeLinecap: "stroke-linecap",
  strokeLinejoin: "stroke-linejoin",
  shapeRendering: "shape-rendering",
  className: "class"
};
const PAINT_PROPERTIES = ["fill", "stroke", "stroke-width", "stroke-linecap", "stroke-linejoin"];

function attributes(markup) {
  const found = {};
  for (const [, name, value] of markup.matchAll(/([\w:-]+)="([^"]*)"/g)) {
    found[JSX_ATTRIBUTE_NAMES[name] ?? name] = value;
  }
  return found;
}

function declarations(text) {
  const found = {};
  for (const part of text.split(";")) {
    const [name, ...rest] = part.split(":");
    if (rest.length) found[name.trim()] = rest.join(":").trim();
  }
  return found;
}

// Illustrator exports paint as CSS classes; the copies inline it as attributes.
function styleClasses(svg) {
  const block = svg.match(/<style[^>]*>([\s\S]*?)<\/style>/);
  const classes = new Map();
  for (const [, name, body] of (block?.[1] ?? "").matchAll(/\.([\w-]+)\s*\{([^}]*)\}/g)) {
    classes.set(name, declarations(body));
  }
  return classes;
}

// Comparing path data as text would fail on the source's wrapped `d` values, so
// compare the command letters and numbers they actually encode.
function geometry(tag, attrs) {
  if (tag === "circle") return ["circle", Number(attrs.cx), Number(attrs.cy), Number(attrs.r)];
  return ["path", ...(attrs.d.match(/[A-Za-z]|-?\d*\.?\d+/g) ?? [])
    .map((token) => (/[A-Za-z]/.test(token) ? token : Number(token)))];
}

function paint(attrs, classes) {
  const resolved = { fill: "#000000", ...(classes.get(attrs.class) ?? {}) };
  for (const property of PAINT_PROPERTIES) {
    if (attrs[property] !== undefined) resolved[property] = attrs[property];
  }
  return Object.fromEntries(PAINT_PROPERTIES
    .filter((property) => resolved[property] !== undefined)
    .map((property) => [property, resolved[property].toLowerCase()]));
}

function shapes(svg) {
  const classes = styleClasses(svg);
  return [...svg.matchAll(/<(path|circle)\b([^>]*?)\/>/g)].map(([, tag, rest]) => {
    const attrs = attributes(rest);
    return { geometry: geometry(tag, attrs), paint: paint(attrs, classes) };
  });
}

function viewBox(svg) {
  return svg.match(/viewBox="([^"]*)"/)[1].trim().split(/[\s,]+/).map(Number);
}

const source = await readFile(SOURCE_URL, "utf8");
const favicon = await readFile(FAVICON_URL, "utf8");
// The sidebar mark is inlined, so read it back out of the component that renders it.
const sidebar = (await readFile(DASHBOARD_URL, "utf8"))
  .match(/function Logo\(\) \{\s*return (<svg[\s\S]*?<\/svg>);/)[1];

test("the favicon draws exactly the shapes and colors of the source asset", () => {
  assert.deepEqual(shapes(favicon), shapes(source));
});

test("the sidebar mark draws exactly the shapes and colors of the source asset", () => {
  assert.deepEqual(shapes(sidebar), shapes(source));
});

test("the source asset still supplies artwork to compare against", () => {
  const drawn = shapes(source);
  assert.equal(drawn.length, 11);
  // Guards against a paint regression that a self-consistent rewrite would hide:
  // the head fill, its outline, and the white question mark all have to survive.
  assert.deepEqual([...new Set(drawn.map((shape) => shape.paint.fill))].sort(),
    ["#94db23", "#f25522", "#ffffff", "none"]);
  assert.equal(drawn.filter((shape) => shape.paint.stroke === "#ffffff").length, 1);
});

test("the sidebar mark keeps the artwork's own aspect ratio", () => {
  const [, , width, height] = viewBox(sidebar);
  assert.deepEqual([width, height], viewBox(source).slice(2));
  const attrs = attributes(sidebar.slice(0, sidebar.indexOf(">")));
  // "xMidYMid meet" letterboxes rather than distorting, but a box that no longer
  // matches the artwork wastes sidebar space, so keep the drift under a percent.
  assert.ok(Math.abs((Number(attrs.width) / Number(attrs.height)) / (width / height) - 1) < 0.01);
});

test("the favicon pads the artwork into a square box without cropping it", () => {
  const [minX, minY, width, height] = viewBox(favicon);
  const [sourceMinX, sourceMinY, sourceWidth, sourceHeight] = viewBox(source);
  // Browsers paint favicons into a square; an unpadded box would letterbox the
  // mark smaller than it has to be at 16px.
  assert.equal(width, height);
  assert.deepEqual([minX, width], [sourceMinX, sourceWidth]);
  assert.ok(Math.abs(minY - (sourceMinY - (height - sourceHeight) / 2)) < 0.005);
  const attrs = attributes(favicon.slice(0, favicon.indexOf(">")));
  assert.equal(attrs.width, attrs.height);
});
