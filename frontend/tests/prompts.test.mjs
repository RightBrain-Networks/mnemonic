import assert from "node:assert/strict";
import test from "node:test";
import {
  PROMPT_IDS, decodePromptDetail, decodePromptLibrary, decodeRenderedPrompt,
  invalidPromptBody, promptPath, promptQueryKeys, validPromptContent, renderedPrompt
} from "../lib/prompts.ts";
import { allowedQueryKeys, browserTransportEffect, invalidMutationBody } from "../lib/proxy-policy.ts";

const project = "11111111-1111-4111-8111-111111111111";
const work = "22222222-2222-4222-8222-222222222222";
const content = "# Instructions\nRead $WORK_ITEM_ID. 🧠\n";
const metadata = (id) => ({ id, name: id, description: "Project agent instructions.",
  size_bytes: new TextEncoder().encode(content).length,
  created_at: "2026-09-12T12:00:00Z", updated_at: "2026-09-12T12:00:00Z", revision: "a".repeat(64) });
const detail = { ...metadata("recall-pointer"), content };
const list = { items: PROMPT_IDS.map(metadata), macros: [{ macro: "$WORK_ITEM_ID", description: "The work ID." }] };
const path = `projects/${project}/prompts/recall-pointer`;

test("prompt library validates complete catalog, unique glossary, and exact content byte sizes", () => {
  assert.deepEqual(decodePromptLibrary(list), list);
  assert.deepEqual(decodePromptDetail(detail, "recall-pointer"), detail);
  for (const changed of [
    { items: list.items.slice(1) }, { items: [...list.items.slice(1), list.items[1]] },
    { macros: [...list.macros, list.macros[0]] }, { macros: [{ macro: "WORK", description: "Missing sigil" }] },
    { extra: "unexpected" }
  ]) assert.throws(() => decodePromptLibrary({ ...list, ...changed }));
  for (const changed of [
    { id: "resume-work" }, { size_bytes: content.length }, { content: "" },
    { revision: "1" }, { updated_at: "2026-02-30T12:00:00Z" }, { extra: "unexpected" }
  ]) assert.throws(() => decodePromptDetail({ ...detail, ...changed }, "recall-pointer"));
});

test("prompt proxy exposes only catalog routes, bounded revision checked writes, and safe renders", () => {
  assert.equal(promptPath(project, "recall-pointer"), `/${path}`);
  assert.deepEqual(allowedQueryKeys(`projects/${project}/prompts`, "GET"), []);
  assert.deepEqual(allowedQueryKeys(path, "GET"), []);
  assert.deepEqual(allowedQueryKeys(path, "PUT"), []);
  assert.deepEqual(allowedQueryKeys(`${path}/render`, "POST"), []);
  for (const [route, method] of [
    [path, "PATCH"], [path, "DELETE"], [`${path}/render`, "GET"],
    [`projects/${project}/prompts/custom`, "PUT"], [`projects/${project}/prompts`, "POST"]
  ]) assert.equal(promptQueryKeys(route, method), null);
  const body = { content, expected_revision: detail.revision };
  assert.equal(invalidMutationBody(path, "PUT", body), null);
  for (const value of [ { content }, { ...body, expected_revision: "1" }, { ...body, extra: true },
    { ...body, content: "\0" }, { ...body, content: "x".repeat(100_001) } ]) {
    assert.ok(invalidPromptBody(path, "PUT", value));
  }
  assert.equal(invalidMutationBody(`${path}/render`, "POST", { work_item_id: work }), null);
  assert.equal(browserTransportEffect(`${path}/render`, "POST"), "safe_read");
  for (const value of [{ work_item_id: "bad" }, { code_review_id: null }, { content }, { lease_token: "secret" }]) {
    assert.ok(invalidMutationBody(`${path}/render`, "POST", value));
  }
});

test("all prompt templates permit macros while report authoring retains its tighter bounds", () => {
  for (const id of PROMPT_IDS) assert.equal(validPromptContent(id, "$WORK_ITEM_TITLE $UNKNOWN"), true);
  assert.equal(validPromptContent("recall-pointer", "x".repeat(100_000)), true);
  assert.equal(validPromptContent("job-completion-report", "x".repeat(8_001)), false);
  assert.equal(validPromptContent("job-completion-report", "🧠".repeat(4_097)), false);
  assert.equal(validPromptContent("review-recommendation", "x".repeat(4_001)), false);
  assert.equal(validPromptContent("review-recommendation", "🧠".repeat(2_049)), false);
  assert.equal(validPromptContent("recall-pointer", "\ud800"), false);
  assert.equal(decodeRenderedPrompt({ content }), content);
  assert.throws(() => decodeRenderedPrompt({ content: "x".repeat(100_001) }));
  assert.throws(() => decodeRenderedPrompt({ content, extra: "unexpected" }));
});

test("agent clipboard content comes from the current backend template and never falls back on failed rendering", async () => {
  const originalFetch = globalThis.fetch;
  const seen = [];
  try {
    globalThis.fetch = async (url, init) => {
      seen.push([url, init]);
      return Response.json({ content: `Externally edited instructions for ${work}.` });
    };
    assert.equal(await renderedPrompt(project, "recall-pointer", work), `Externally edited instructions for ${work}.`);
    assert.equal(seen[0][0], `/api/mnemonic/${path}/render`);
    assert.equal(seen[0][1].method, "POST");
    assert.deepEqual(JSON.parse(seen[0][1].body), { work_item_id: work });
    globalThis.fetch = async () => Response.json({ detail: { code: "prompt_unavailable", message: "Prompt unavailable." } }, { status: 503 });
    await assert.rejects(renderedPrompt(project, "recall-pointer", work), /Prompt unavailable/);
    globalThis.fetch = async () => Response.json({ content: "" });
    await assert.rejects(renderedPrompt(project, "recall-pointer", work), /invalid prompt response/);
  } finally { globalThis.fetch = originalFetch; }
});
