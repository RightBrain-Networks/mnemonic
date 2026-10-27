import assert from "node:assert/strict";
import test from "node:test";
import { assertTagMutationResponse, decodeTagCatalog, normalizeTagName, tagPalette, tagTextColor, workTagNames } from "../lib/tags.ts";
import { workSearchRequest } from "../lib/unified-search.ts";
import { workSearchParams, childSearchParams } from "../lib/work-item-search.ts";
import { validSearchRequest } from "../lib/search-request.ts";

const project = "7a5dc555-0a6d-4f92-9678-1647524827c8";
const tag = { id: "e36a7e53-938f-4c8a-b75a-af9c7331711a", project_id: project, name: "needs-design", description: "Work that needs design input.", color: "#a8c8e8", menu_order: 1, system: false };

test("catalog validates ownership, normalized names, duplicate identities and saved colors", () => {
  assert.equal(normalizeTagName("  Needs DESIGN / Feedback "), "needs-design-feedback");
  assert.deepEqual(decodeTagCatalog({ project_id: project, items: [tag], total: 1 }, project).items, [tag]);
  for (const patch of [{ name: "Uppercase" }, { name: "two words" }, { color: "red" }, { menu_order: -1 }, { description: "x".repeat(241) }, { project_id: tag.id }]) {
    assert.throws(() => decodeTagCatalog({ project_id: project, items: [{ ...tag, ...patch }], total: 1 }, project));
  }
  assert.throws(() => decodeTagCatalog({ project_id: project, items: [tag, tag], total: 2 }, project));
  assert.throws(() => decodeTagCatalog({ project_id: project, items: [tag], total: 2 }, project));
});

test("system classifications remain visible with null assignments and do not duplicate selected labels", () => {
  assert.deepEqual(workTagNames({ tags: null, system_tags: ["dir"] }), ["dir"]);
  assert.deepEqual(workTagNames({ tags: ["dir", "needs-design"], system_tags: ["dir"] }), ["dir", "needs-design"]);
});

test("custom colors always choose a WCAG AA contrasting foreground", () => {
  const luminance = (hex) => {
    const channels = hex.slice(1).match(/.{2}/g).map((value) => parseInt(value, 16) / 255).map((value) => value <= .04045 ? value / 12.92 : ((value + .055) / 1.055) ** 2.4);
    return channels[0] * .2126 + channels[1] * .7152 + channels[2] * .0722;
  };
  for (const color of [...tagPalette, "#000000", "#ffffff", "#777777", "#ff0000", "#0000ff"]) {
    const values = [luminance(color), luminance(tagTextColor(color))].sort((a, b) => b - a);
    assert.ok((values[0] + .05) / (values[1] + .05) >= 4.5, color);
  }
});

test("tag conjunctions retain their exact scope across search and hierarchy requests", () => {
  const input = { status: "all", sort: "updated", limit: 20, offset: 0, query: "", tags: ["needs-design", "dir"], tagMode: "and" };
  const request = workSearchRequest(input);
  assert.deepEqual(request.filters.work_items.tags, input.tags);
  assert.equal(request.filters.work_items.tag_mode, "and");
  assert.equal(validSearchRequest(request), true);
  assert.equal(validSearchRequest({ ...request, filters: { work_items: { tags: ["Wrong Name"] } } }), false);
  for (const params of [workSearchParams(input), childSearchParams(input)]) {
    assert.deepEqual(params.getAll("tags"), input.tags);
    assert.equal(params.get("tag_mode"), "and");
  }
  assert.equal(workSearchRequest({ ...input, tags: [] }).filters.work_items.tags, undefined);
});

test("omitted default filter fields still validate All and OR without accepting a lost conjunction", async () => {
  const { disclosure } = await import("./search-disclosure-fixtures.mjs");
  const { decodeSearchDisclosure, validateSearchDisclosure } = await import("../lib/search-disclosure.ts");
  const decoded = decodeSearchDisclosure(disclosure(project, ["work_items"]), project, ["work_items"]);
  assert.doesNotThrow(() => validateSearchDisclosure(decoded, "work_items", { tags: null, tag_mode: "or" }));
  assert.throws(() => validateSearchDisclosure(decoded, "work_items", { tags: ["dir"], tag_mode: "and" }));
});


test("catalog write confirmations must match the frozen target and every requested change", () => {
  const catalog = { project_id: project, items: [tag], total: 1 };
  const path = `/projects/${project}/tags`;
  const fields = { name: " Needs Design ", description: tag.description, color: tag.color.toUpperCase(), client_operation_id: crypto.randomUUID() };
  assert.doesNotThrow(() => assertTagMutationResponse(catalog, "POST", path, fields));
  assert.doesNotThrow(() => assertTagMutationResponse(catalog, "PATCH", `${path}/${tag.id}`, fields));
  assert.doesNotThrow(() => assertTagMutationResponse(catalog, "PUT", `${path}/order`, { tag_ids: [tag.id] }));
  assert.doesNotThrow(() => assertTagMutationResponse({ ...catalog, items: [], total: 0 }, "DELETE", `${path}/${tag.id}`, {}));
  for (const changed of [{ name: "different" }, { description: "stale description" }, { color: "#ffffff" }, { system: true }]) {
    const wrong = { ...catalog, items: [{ ...tag, ...changed }] };
    assert.throws(() => assertTagMutationResponse(wrong, "POST", path, fields));
    assert.throws(() => assertTagMutationResponse(wrong, "PATCH", `${path}/${tag.id}`, fields));
  }
  assert.throws(() => assertTagMutationResponse(catalog, "PATCH", `${path}/${project}`, fields));
  assert.throws(() => assertTagMutationResponse(catalog, "DELETE", `${path}/${tag.id}`, {}));
  assert.throws(() => assertTagMutationResponse(catalog, "PUT", `${path}/order`, { tag_ids: [] }));
  assert.throws(() => assertTagMutationResponse(catalog, "PUT", `${path}/order`, { tag_ids: [project] }));
});
