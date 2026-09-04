import assert from "node:assert/strict";
import { readFile } from "node:fs/promises";
import test from "node:test";
import {
  ApiError,
  completionEvidenceApi
} from "../lib/api.ts";

const project = "e36a7e53-938f-4c8a-b75a-af9c7331711a";
const work = "7a5dc555-0a6d-4f92-9678-1647524827c8";
const path = `/projects/${project}/work-items/${work}/completion-evidence?limit=10`;

function emptyPage() {
  return {
    work_item_id: work,
    work_version: 3,
    lifecycle_status: "pending",
    is_duplicate: false,
    canonical_work_item_id: work,
    current_completion_checkpoint_id: null,
    as_of_completion_event_id: null,
    items: [],
    total: 0,
    structured_completion_total: 0,
    limit: 10,
    next_cursor: null
  };
}

test("browser evidence API uses the bounded identity reader rather than response.json", async () => {
  const original = globalThis.fetch;
  let calls = 0;
  try {
    globalThis.fetch = async (url, init) => {
      calls += 1;
      assert.equal(url, `/api/mnemonic${path}`);
      assert.equal(init.credentials, "same-origin");
      assert.equal(init.cache, "no-store");
      const response = new Response(JSON.stringify(emptyPage()), {
        headers: {
          "Content-Type": "application/json",
          "Content-Encoding": "IdEnTiTy"
        }
      });
      response.json = () => {
        throw new Error("response.json must not be used for evidence");
      };
      return response;
    };
    assert.deepEqual(await completionEvidenceApi(path, work), emptyPage());
    assert.equal(calls, 1);
  } finally {
    globalThis.fetch = original;
  }
});

test("browser evidence API rejects coded success and error bodies before getReader", async () => {
  const original = globalThis.fetch;
  let readerCalls = 0;
  let cancelCalls = 0;
  try {
    globalThis.fetch = async () => ({
      ok: false,
      status: 422,
      headers: new Headers({
        "Content-Type": "application/json",
        "Content-Encoding": "gzip"
      }),
      body: {
        getReader() {
          readerCalls += 1;
          throw new Error("must not acquire reader");
        },
        async cancel() { cancelCalls += 1; }
      },
      json() {
        throw new Error("must not parse coded body");
      }
    });
    await assert.rejects(
      completionEvidenceApi(path, work),
      (error) => (
        error instanceof ApiError
        && error.status === 0
        && !error.message.includes("attacker")
      )
    );
    assert.equal(readerCalls, 0);
    assert.equal(cancelCalls, 1);
  } finally {
    globalThis.fetch = original;
  }
});

test("browser evidence API parses bounded identity error details through the existing sanitizer", async () => {
  const original = globalThis.fetch;
  try {
    globalThis.fetch = async () => new Response(JSON.stringify({
      detail: {
        code: "invalid_cursor",
        message: "The evidence cursor is invalid.",
        context: { command: "attacker text", holder_client: "safe-client" }
      }
    }), {
      status: 422,
      headers: { "Content-Type": "application/json" }
    });
    await assert.rejects(
      completionEvidenceApi(path, work),
      (error) => (
        error instanceof ApiError
        && error.status === 422
        && error.code === "invalid_cursor"
        && error.message === "The evidence cursor is invalid. safe-client"
        && !error.message.includes("attacker")
      )
    );
  } finally {
    globalThis.fetch = original;
  }
});

test("Next proxy requests identity and never uses unbounded arrayBuffer for evidence", async () => {
  const route = await readFile(
    new URL("../app/api/mnemonic/[...path]/route.ts", import.meta.url),
    "utf8"
  );
  assert.match(route, /evidenceRoute \? \{ "Accept-Encoding": "identity" \}/);
  assert.match(route, /readIdentityEvidenceBytes\(upstream\)/);
  assert.match(route, /identityContentEncoding\(upstream\.headers\)/);
  assert.match(route, /evidenceRoute \? \{ "Content-Encoding": "identity" \}/);
  assert.match(route, /no-store, max-age=0, no-transform/);
  const evidenceBranch = route.slice(
    route.indexOf("if (evidenceRoute)"),
    route.indexOf("} else {", route.indexOf("if (evidenceRoute)"))
  );
  assert.doesNotMatch(evidenceBranch, /arrayBuffer\(/);
});
