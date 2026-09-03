import assert from "node:assert/strict";
import { readFile } from "node:fs/promises";
import test from "node:test";
import {
  allowedQueryKeys,
  browserTransportEffect,
  forwardedRetryAfter,
  invalidMutationBody,
  proxyBodyLimitBytes,
  readBodyChunk,
  upstreamAbortSignal,
  upstreamTimeoutMs
} from "../lib/proxy-policy.ts";

const project = "e36a7e53-938f-4c8a-b75a-af9c7331711a";
const work = "7a5dc555-0a6d-4f92-9678-1647524827c8";
const operation = "91b9168a-37d1-4a6a-aa1f-bb538b65cb55";

test("Advisory proxy exposes one exact safe-read POST and only its six body fields", () => {
  const path = `projects/${project}/duplicate-suggestions`;
  const body = {
    title: "Durable objective",
    summary: "Compare this objective before creating it.",
    initial_prompt: "Full initial context.",
    tags: ["phase-9", "advisory"],
    exclude_work_item_id: null,
    limit: 5
  };
  assert.deepEqual(allowedQueryKeys(path, "POST"), []);
  assert.equal(allowedQueryKeys(path, "GET"), null);
  assert.equal(allowedQueryKeys(path, "PATCH"), null);
  assert.equal(allowedQueryKeys(path, "DELETE"), null);
  assert.equal(invalidMutationBody(path, "POST", body), null);
  assert.equal(invalidMutationBody(path, "POST", {
    title: body.title,
    summary: body.summary,
    initial_prompt: body.initial_prompt
  }), null, "server-defaulted fields remain optional");
  assert.equal(browserTransportEffect(path, "POST"), "safe_read");

  for (const invalid of [
    { ...body, client_operation_id: operation },
    { ...body, lease_token: "private" },
    { ...body, provenance: { source_client: "dashboard" } },
    { ...body, canonical_work_item_id: work },
    { ...body, create: true },
    { ...body, tags: Array.from({ length: 21 }, (_, index) => `tag-${index}`) },
    { ...body, tags: ["x".repeat(51)] },
    { ...body, exclude_work_item_id: "not-a-uuid" },
    { ...body, limit: 0 },
    { ...body, limit: 11 },
    { ...body, nested: { client_operation_id: operation } }
  ]) assert.ok(invalidMutationBody(path, "POST", invalid));
});

test("only the Advisory route receives the two-MiB streaming allowance", () => {
  const path = `projects/${project}/duplicate-suggestions`;
  assert.equal(proxyBodyLimitBytes(path), 2_097_152);
  assert.equal(proxyBodyLimitBytes(`projects/${project}/work-items`), 1_048_576);
  assert.equal(proxyBodyLimitBytes(`${path}/nested`), 1_048_576);

  const astralPrompt = "🧠".repeat(100_000);
  const escaped = JSON.stringify({
    title: "Astral boundary",
    summary: "The maximum prompt remains valid.",
    initial_prompt: astralPrompt,
    tags: [],
    exclude_work_item_id: null,
    limit: 5
  }).replaceAll("🧠", "\\ud83e\\udde0");
  const encodedSize = new TextEncoder().encode(escaped).byteLength;
  assert.ok(encodedSize > proxyBodyLimitBytes(`projects/${project}/work-items`));
  assert.ok(encodedSize < proxyBodyLimitBytes(path));
  assert.equal(invalidMutationBody(path, "POST", JSON.parse(escaped)), null);
});

test("Advisory timeout and sanitized Retry-After forwarding are route specific", () => {
  const path = `projects/${project}/duplicate-suggestions`;
  assert.equal(upstreamTimeoutMs(new URLSearchParams(), path, "POST"), 60_000);
  assert.equal(upstreamTimeoutMs(new URLSearchParams(), path, "GET"), 15_000);
  assert.equal(forwardedRetryAfter(429, new Headers({ "Retry-After": "1" })), "1");
  assert.equal(
    forwardedRetryAfter(429, new Headers({ "Retry-After": "Wed, 21 Oct 2037 07:28:00 GMT" })),
    "Wed, 21 Oct 2037 07:28:00 GMT"
  );
  assert.equal(forwardedRetryAfter(503, new Headers({ "Retry-After": "1" })), null);
  assert.equal(forwardedRetryAfter(429, new Headers({ "Retry-After": "not valid" })), null);
});

test("Advisory upstream cancellation follows the browser request signal", () => {
  const path = `projects/${project}/duplicate-suggestions`;
  const requestController = new AbortController();
  const reason = new Error("browser request was cancelled");
  const signal = upstreamAbortSignal(
    requestController.signal,
    new URLSearchParams(),
    path,
    "POST"
  );

  assert.equal(signal.aborted, false);
  requestController.abort(reason);
  assert.equal(signal.aborted, true);
  assert.equal(signal.reason, reason);
});

test("one Advisory deadline spans a stalled body read and the upstream fetch", async () => {
  const routeSource = await readFile(
    new URL("../app/api/mnemonic/[...path]/route.ts", import.meta.url),
    "utf8"
  );
  assert.equal(routeSource.match(/upstreamAbortSignal\(/g)?.length, 1);
  assert.match(routeSource, /readBody\(request, route, requestSignal\)/);
  assert.match(routeSource, /signal: requestSignal/);

  const reason = new Error("synthetic client cancellation");
  let cancelledWith;
  const stream = new ReadableStream({
    pull: () => new Promise(() => undefined),
    cancel: (value) => { cancelledWith = value; }
  });
  const controller = new AbortController();
  const signal = upstreamAbortSignal(
    controller.signal,
    new URLSearchParams(),
    `projects/${project}/duplicate-suggestions`,
    "POST"
  );
  const stalledRead = readBodyChunk(stream.getReader(), signal);
  controller.abort(reason);
  await assert.rejects(stalledRead, (error) => error === reason);
  assert.equal(cancelledWith, reason);
});
