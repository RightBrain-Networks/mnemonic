import { ranking, hitRanking, unifiedRanking, semanticDisposition, evidence } from "./search-ranking-fixtures.mjs";
import { disclosure } from "./search-disclosure-fixtures.mjs";
import assert from "node:assert/strict";
import test from "node:test";
import { decodeTranscriptProxyRejection, decodeTranscript, decodeTranscriptPage, decodeTranscriptSettings, decodeTranscriptText, transcriptContentPath, transcriptLibraryPath, transcriptRequest, transcriptStatusLabel, transcriptClientLabel, TRANSCRIPT_JSON_MAX_BYTES, TRANSCRIPT_MAX_BYTES } from "../lib/transcripts.ts";
import { proxyTranscript, readTranscriptMutationBody, transcriptRoute, validTranscriptQuery } from "../lib/transcript-proxy.ts";
const project = "7a5dc555-0a6d-4f92-9678-1647524827c8";
const id = "e36a7e53-938f-4c8a-b75a-af9c7331711a";
const operation = "91b9168a-37d1-4a6a-aa1f-bb538b65cb55";
const digest = "a".repeat(64);
const root = `projects/${project}/transcripts`;
const row = { id, project_id: project, work_item_id: operation, lease_generation_id: operation, client: "claude-code", session_id: "session-1", source_path: "/shared/session.jsonl", filename: "session.jsonl", kind: "primary", status: "ready", indexing_started_at: "2026-09-10T12:00:00Z", indexing_completed_at: "2026-09-10T12:00:01Z", error_code: null, size_bytes: 128, mime_type: "application/x-ndjson", format: "claude-code-jsonl", sha256: digest, text_sha256: digest, metadata: { title: ["<script>untrusted</script>"] }, truncated: false, created_at: "2026-09-10T12:00:00Z", snippet: null, score: null };
Object.assign(row, { normalization_status: "ready", normalization_error_code: null,
  normalized_revision: "b".repeat(64), normalized_sha256: "c".repeat(64), normalization_schema_version: 1,
  normalizer_version: 1, normalized_size_bytes: 400, segment_count: 2, normalization_incomplete: false,
  segment_id: null, content_kind: null, snippet_omission_reason: null, matched_fields: [], rank: 1, score_type: "none" });
Object.assign(row, { copy_status: "ready", copy_error_code: null, copied_at: "2026-09-10T12:00:00Z", index_status: "ready", index_error_code: null });
const listing = { ...ranking("", "transcripts"), unsegmented_content_omitted: 0, detail: "full", ...disclosure(project, ["transcripts"]), term_diagnostics: [], items: [row], total: 1, limit: 50, offset: 0, indexing_incomplete: false };
const environment = { MNEMONIC_API_KEY: "k".repeat(64), MNEMONIC_API_URL: "http://api:8000" };
const request = (path, method = "GET", value, headers = {}) => new Request(`http://localhost:3000/api/transcripts/${path}`, { method, headers: { host: "localhost:3000", ...(method !== "GET" ? { origin: "http://localhost:3000", "content-type": "application/json" } : {}), ...headers }, ...(value === undefined ? {} : { body: typeof value === "string" ? value : JSON.stringify(value) }) });

test("transcript metadata and listings enforce project, work, pagination and content scope", () => {
  assert.equal(decodeTranscript(row, project).metadata.title[0], "<script>untrusted</script>");
  assert.equal(decodeTranscriptPage(listing, project).total, 1);
  for (const change of [{ project_id: id }, { work_item_id: "bad" }, { lease_generation_id: "bad" }, { status: "done" }, { size_bytes: -1 }, { indexing_completed_at: "tomorrow" }, { text_sha256: "bad" }, { metadata: { script: "bad" } }]) assert.throws(() => decodeTranscript({ ...row, ...change }, project));
  for (const change of [{ items: [row, row], total: 2 }, { total: 2 }, { offset: 50 }, { items: [{ ...row, snippet: "content" }] }]) assert.throws(() => decodeTranscriptPage({ ...listing, ...change }, project));
  assert.throws(() => decodeTranscriptPage(listing, project, 0, false, id));
  assert.equal(decodeTranscriptPage({ ...listing, ...disclosure(project, ["transcripts"], { fulltext: true }), items: [{ ...row, snippet: "<script>plain</script>" }] }, project, 0, true).items[0].snippet, "<script>plain</script>");
  assert.equal(transcriptLibraryPath(project, operation), `/transcripts?project=${project}&work=${operation}`);
  assert.match(transcriptContentPath(row), new RegExp(`expected_sha256=${digest}$`));
});

test("preview validates unicode pagination and rejects stale indexed text", () => {
  const text = { normalized_revision: null, segments: null, segment_window: null, next_segment_id: null, next_segment_offset: null, next_segment_after: null, transcript_id: id, project_id: project, next_offset: null, text: "Hi 😀", total_chars: 4, offset: 0, limit: 20000, status: "ready", truncated: false, text_sha256: digest };
  assert.equal(decodeTranscriptText(text, id, digest, 0, project).text, "Hi 😀");
  for (const change of [{ text_sha256: "b".repeat(64) }, { total_chars: 5 }, { offset: 1 }, { status: "pending" }, { transcript_id: project }]) assert.throws(() => decodeTranscriptText({ ...text, ...change }, id, digest, 0, project));
});

test("settings enforce size limits and typed shared folder paths", () => {
  const settings = { enabled: true, max_file_size_bytes: 67108864, revision: 1, operator_max_file_size_bytes: TRANSCRIPT_MAX_BYTES, allowed_roots: ["/shared/transcripts"] };
  assert.equal(decodeTranscriptSettings(settings).revision, 1);
  for (const change of [{ revision: 0 }, { enabled: "true" }, { max_file_size_bytes: TRANSCRIPT_MAX_BYTES + 1 }, { max_file_size_bytes: 0 }, { allowed_roots: [null] }]) assert.throws(() => decodeTranscriptSettings({ ...settings, ...change }));
});

test("proxy exposes only bounded project routes and canonical query fields", () => {
  assert.equal(transcriptRoute(root.split("/"), "GET"), "list");
  assert.equal(transcriptRoute(`${root}/rebuild`.split("/"), "POST"), "rebuild");
  assert.equal(transcriptRoute(root.split("/"), "POST"), null);
  assert.equal(transcriptRoute(`${root}/${id}/content`.split("/"), "DELETE"), null);
  for (const query of ["offset=-1", "limit=101", "limit=50&limit=50", "lease_token=secret", "work_item_id=bad", "fulltext=yes", "query=%20"]) assert.equal(validTranscriptQuery(new URLSearchParams(query), "list"), false);
  assert.equal(validTranscriptQuery(new URLSearchParams("query=hello&fulltext=true&limit=50&offset=0"), "list"), true);
});

test("writes reject CSRF, control headers and malformed settings before forwarding", async () => {
  let calls = 0;
  const fetcher = async () => { calls++; return Response.json({}); };
  const path = `projects/${project}/transcript-settings`;
  const body = { enabled: true, max_file_size_bytes: 1024, expected_revision: 1 };
  for (const [input, status] of [
    [request(path, "PATCH", body, { origin: "https://attacker.example" }), 403],
    [request(path, "PATCH", body, { "X-Lease-Token": "secret" }), 400],
    [request(path, "PATCH", { ...body, allowed_roots: ["/"] }), 400],
    [request(path, "PATCH", { ...body, max_file_size_bytes: 0 }), 400],
    [request(path, "PATCH", body, { "content-encoding": "gzip" }), 415],
    [request(path, "PATCH", "x".repeat(4097)), 400]
  ]) assert.equal((await proxyTranscript(input, path.split("/"), environment, fetcher)).status, status);
  assert.equal((await proxyTranscript(request(`${root}/rebuild`, "POST", {}), `${root}/rebuild`.split("/"), environment, fetcher)).status, 400);
  assert.equal(calls, 0);
});

test("rebuild forwards the exact durable operation request without rewriting retries", async () => {
  const body = `{ "client_operation_id": "${operation}" }`;
  const result = await proxyTranscript(request(`${root}/rebuild`, "POST", body), `${root}/rebuild`.split("/"), environment, async (target, init) => {
    assert.equal(String(target), `http://api:8000/api/v1/${root}/rebuild`);
    assert.equal(init.body, body);
    assert.equal(init.headers.Authorization, `Bearer ${environment.MNEMONIC_API_KEY}`);
    assert.equal(init.redirect, "manual");
    return Response.json({ queued: 3 });
  });
  assert.deepEqual(await result.json(), { queued: 3 });
});

test("downloads are bounded sandboxed attachments without executable upstream headers", async () => {
  const path = `${root}/${id}/content`;
  const result = await proxyTranscript(request(path), path.split("/"), environment, async () => new Response("<script>untrusted</script>", { headers: { "content-type": "text/html", "set-cookie": "secret=1", "content-disposition": "inline" } }));
  assert.equal(result.status, 200);
  assert.equal(result.headers.get("content-type"), "application/octet-stream");
  assert.equal(result.headers.get("set-cookie"), null);
  assert.match(result.headers.get("content-disposition"), /^attachment;/);
  assert.match(result.headers.get("content-security-policy"), /sandbox/);
  assert.equal(await result.text(), "<script>untrusted</script>");
  for (const response of [new Response("", { status: 302, headers: { location: "https://attacker.example" } }), new Response("partial", { status: 206 }), new Response("encoded", { headers: { "content-encoding": "gzip" } })]) assert.equal((await proxyTranscript(request(path), path.split("/"), environment, async () => response)).status, 502);
});


test("only exact proxy-owned rejection envelopes prove a fresh rebuild was not dispatched", async () => {
  const path = `${root}/rebuild`;
  const response = await proxyTranscript(request(path, "POST", {}), path.split("/"), environment, async () => { throw new Error("must not reach upstream"); });
  const value = await response.json();
  assert.equal(response.status, 400);
  assert.equal(decodeTranscriptProxyRejection(response.status, value), "Invalid transcript settings request.");
  for (const [status, body] of [
    [500, value], [503, value], [409, value],
    [400, { detail: "Invalid transcript settings request." }],
    [400, { detail: { code: "transcript_proxy_rejected", message: "Unknown rejection." } }],
    [400, { ...value, extra: true }],
    [400, { detail: { ...value.detail, extra: true } }]
  ]) assert.equal(decodeTranscriptProxyRejection(status, body), null);
  const upstream = await proxyTranscript(request(path, "POST", { client_operation_id: operation }), path.split("/"), environment, async () => Response.json({ detail: "Invalid transcript settings request." }, { status: 400 }));
  assert.equal(decodeTranscriptProxyRejection(upstream.status, await upstream.json()), null);
  const forged = await proxyTranscript(request(path, "POST", { client_operation_id: operation }), path.split("/"), environment, async () => Response.json(value, { status: 400 }));
  assert.equal(forged.status, 502);
  assert.equal(decodeTranscriptProxyRejection(forged.status, await forged.json()), null);
});

test("stalled transcript mutation bodies time out and cancel without waiting for source cancellation", async () => {
  let cancelled = false;
  const stream = new ReadableStream({
    start(controller) { controller.enqueue(new TextEncoder().encode('{"client_operation_id":')); },
    cancel() { cancelled = true; return new Promise(() => {}); }
  });
  const input = new Request(`http://localhost:3000/api/transcripts/${root}/rebuild`, {
    method: "POST", body: stream, duplex: "half",
    headers: { host: "localhost:3000", origin: "http://localhost:3000", "content-type": "application/json" }
  });
  await assert.rejects(readTranscriptMutationBody(input, 10), { name: "TimeoutError" });
  assert.equal(cancelled, true);
  assert.equal(input.body.locked, false);
});

test("disconnected transcript writes cancel their incoming stream before upstream dispatch", async () => {
  for (const alreadyAborted of [false, true]) {
    const controller = new AbortController();
    let cancelled = false;
    let calls = 0;
    const stream = new ReadableStream({ cancel() { cancelled = true; } });
    const input = new Request(`http://localhost:3000/api/transcripts/${root}/rebuild`, {
      method: "POST", body: stream, duplex: "half", signal: controller.signal,
      headers: { host: "localhost:3000", origin: "http://localhost:3000", "content-type": "application/json" }
    });
    if (alreadyAborted) controller.abort();
    const pending = proxyTranscript(input, `${root}/rebuild`.split("/"), environment, async () => { calls++; return Response.json({}); });
    if (!alreadyAborted) controller.abort();
    const response = await pending;
    assert.equal(response.status, 400);
    assert.equal(decodeTranscriptProxyRejection(response.status, await response.json()), "Invalid or oversized transcript settings request.");
    assert.equal(cancelled, true);
    assert.equal(calls, 0);
    assert.equal(input.body.locked, false);
  }
});

test("transcript page budgets accommodate valid long Unicode paths and escaped JSON", async (context) => {
  const filename = "😀".repeat(4095);
  const page = { ...listing, total: 50, items: Array.from({ length: 50 }, (_, index) => ({
    ...row, id: `${id.slice(0, -12)}${index.toString(16).padStart(12, "0")}`,
    filename, source_path: `/${filename}`
  })) };
  const encoded = JSON.stringify(page).replace(/[^\x00-\x7f]/g, (character) => `\\u${character.charCodeAt(0).toString(16).padStart(4, "0")}`);
  assert.ok(Buffer.byteLength(encoded) > 4 * 1024 * 1024);
  assert.ok(Buffer.byteLength(encoded) < TRANSCRIPT_JSON_MAX_BYTES);
  const upstream = () => new Response(encoded, { headers: { "content-type": "application/json" } });
  const response = await proxyTranscript(request(root), root.split("/"), environment, upstream);
  assert.equal(response.status, 200);
  assert.equal(decodeTranscriptPage(await response.json(), project).items.length, 50);
  context.mock.method(globalThis, "fetch", upstream);
  const browserValue = await transcriptRequest(`/api/transcripts/${root}`);
  assert.equal(decodeTranscriptPage(browserValue, project).items[0].filename, filename);
});

test("imported transcripts require null lease provenance and remain scoped", () => {
  const imported = { ...row, kind: "imported", work_item_id: null, lease_generation_id: null, session_id: null };
  assert.equal(decodeTranscript(imported, project).kind, "imported");
  assert.equal(decodeTranscriptPage({ ...listing, items: [imported] }, project).total, 1);
  for (const change of [{ work_item_id: operation }, { lease_generation_id: operation }, { session_id: "invented" }, { kind: "primary" }]) {
    assert.throws(() => decodeTranscript({ ...imported, ...change }, project));
  }
  assert.throws(() => decodeTranscriptPage({ ...listing, items: [imported] }, project, 0, false, operation));
});

test("import proxy preserves exact requests and rejects unsafe folder paths and CSRF", async () => {
  const path = `${root}/import`;
  let calls = 0;
  const fetcher = async () => { calls++; return Response.json({}); };
  assert.equal(transcriptRoute(path.split("/"), "POST"), "import");
  assert.equal(transcriptRoute(path.split("/"), "GET"), null);
  for (const directory of ["relative", "/allowed/../private", "/path\u0000", "/path\ud800", "x".repeat(4097), null]) {
    assert.equal((await proxyTranscript(request(path, "POST", { directory, client_operation_id: operation }), path.split("/"), environment, fetcher)).status, 400);
  }
  assert.equal((await proxyTranscript(request(path, "POST", { directory: "/shared", client_operation_id: operation }, { origin: "https://attacker.example" }), path.split("/"), environment, fetcher)).status, 403);
  assert.equal(calls, 0);
  const body = `{ "directory": "/shared//./sessions", "client_operation_id": "${operation}" }`;
  const response = await proxyTranscript(request(path, "POST", body), path.split("/"), environment, async (target, init) => {
    assert.equal(String(target), `http://api:8000/api/v1/${path}`);
    assert.equal(init.body, body);
    return Response.json({ imported: 1, existing: 2, skipped: 0 });
  });
  assert.equal(response.status, 200);
});

test("import confirmation binds counts, project, folder and operation", async () => {
  const { decodeTranscriptImport, decodeTranscriptImportRejection } = await import("../lib/transcripts.ts");
  const result = { project_id: project, client_operation_id: operation, directory: "/shared", imported: 1, existing: 2, skipped: 0 };
  assert.equal(decodeTranscriptImport(result, project, operation, "/shared").imported, 1);
  for (const change of [{ project_id: id }, { client_operation_id: id }, { directory: "/other" }, { imported: -1 }, { imported: 5000, existing: 1 }, { skipped: 50001 }, { extra: true }]) {
    assert.throws(() => decodeTranscriptImport({ ...result, ...change }, project, operation, "/shared"));
  }
  const rejection = { detail: { code: "transcript_import_scan_failed", message: "Cannot read this folder.", context: {} } };
  assert.equal(decodeTranscriptImportRejection(422, rejection), rejection.detail.message);
  assert.equal(decodeTranscriptImportRejection(500, rejection), null);
  assert.equal(decodeTranscriptImportRejection(409, { detail: { code: "transcript_import_conflict", message: "Conflict" } }), null);
});

test("Codex transcripts retain client metadata and display their provider name", () => {
  const codex = { ...row, client: "codex", format: "codex-jsonl" };
  assert.equal(decodeTranscript(codex, project).client, "codex");
  assert.equal(transcriptClientLabel(codex.client), "OpenAI Codex");
  assert.equal(transcriptClientLabel("claude_code"), "Claude Code");
  assert.equal(transcriptClientLabel("future-client"), "future-client");
});

test("normalization coverage and matched content kinds remain explicit in transcript results", () => {
  const contentKinds = ["human_text", "assistant_text"];
  const matched = { ...row, snippet: "needle", matched_fields: ["content"], segment_id: "a".repeat(24), content_kind: "human_text" };
  const result = { ...listing, ...disclosure(project, ["transcripts"], { fulltext: true,
    filters: { transcripts: { content_kinds: contentKinds } } }), items: [matched] };
  assert.equal(decodeTranscriptPage(result, project, 0, true, undefined, contentKinds).items[0].segment_id, matched.segment_id);
  for (const change of [{ content_kind: "tool_result" }, { normalized_revision: null }, { segment_id: null },
    { normalization_incomplete: true }, { normalization_status: "failed" }, { normalized_size_bytes: -1 },
    { normalization_schema_version: 0 }, { segment_count: true }]) {
    assert.throws(() => decodeTranscriptPage({ ...result, items: [{ ...matched, ...change }] }, project, 0, true, undefined, contentKinds));
  }
  assert.throws(() => decodeTranscriptPage(result, project, 0, true, undefined, ["tool_result"]));
  assert.equal(decodeTranscriptPage({ ...result, indexing_incomplete: true,
    items: [{ ...matched, normalization_status: "failed", normalization_incomplete: true }] }, project, 0, true, undefined, contentKinds).indexing_incomplete, true);
});

test("the transcript proxy preserves normalized revision pins and bounded context windows", () => {
  const base = `segment_id=${"a".repeat(24)}&expected_normalized_revision=${digest}`;
  assert.equal(validTranscriptQuery(new URLSearchParams(`${base}&offset=8000001&before=0&after=20`), "text"), true);
  assert.equal(validTranscriptQuery(new URLSearchParams(`${base}&expected_sha256=${digest}`), "text"), true);
  for (const query of ["before=1", `expected_normalized_revision=${digest}`, `segment_id=${"a".repeat(24)}`,
    `${base}&before=1&offset=1`, `${base}&before=20&after=1`, `${base}&after=-1`, `${base}&offset=1073741825`,
    `${base}&segment_id=${"a".repeat(24)}`]) assert.equal(validTranscriptQuery(new URLSearchParams(query), "text"), false);
});

test("conversation text responses have a smaller transport budget than discovery listings", async () => {
  const path = `${root}/${id}/text`;
  const response = await proxyTranscript(request(`${path}?expected_sha256=${digest}`), path.split("/"), environment,
    async () => Response.json({ text: "x".repeat(1024 * 1024) }));
  assert.equal(response.status, 502);
});


test("valid escaped Unicode segment windows fit the browser text transport budget", async () => {
  const path = `${root}/${id}/text`;
  const text = "🌲".repeat(20000);
  const encoded = JSON.stringify({ text, segments: [{ text }] }).replace(/[\u007f-\uffff]/g, (character) => `\\u${character.charCodeAt(0).toString(16).padStart(4, "0")}`);
  assert.ok(encoded.length > 256 * 1024);
  const response = await proxyTranscript(request(`${path}?segment_id=${"a".repeat(24)}&expected_normalized_revision=${digest}&limit=20000`), path.split("/"), environment,
    async () => new Response(encoded, { headers: { "Content-Type": "application/json" } }));
  assert.equal(response.status, 200);
  assert.equal((await response.json()).text, text);
});


test("transcript status distinguishes search limits from normalization and metadata warnings", () => {
  assert.equal(transcriptStatusLabel(row), "Indexed");
  assert.equal(transcriptStatusLabel({ ...row, normalization_incomplete: true }), "Indexed · Normalization warnings");
  assert.equal(transcriptStatusLabel({ ...row, truncated: true }), "Indexed · Search text limited");
  assert.equal(transcriptStatusLabel({ ...row, truncated: true, normalization_incomplete: true }), "Indexed · Search text limited · Normalization warnings");
  assert.equal(transcriptStatusLabel({ ...row, metadata: { "transcript:metadata_limited": ["true"] } }), "Indexed · Metadata limited");
});

test("native session metadata validates independently of reporting provenance", () => {
  const metadata = { ...row, last_updated_at: "2026-02-01T14:30:00Z", index_created_at: row.indexing_completed_at, session_ids: ["native-child"], models: ["fixture-model"] };
  assert.equal(decodeTranscript(metadata, project).session_ids[0], "native-child");
  assert.equal(decodeTranscript(metadata, project).session_id, "session-1");
  for (const change of [{last_updated_at: "yesterday"}, {index_created_at: "tomorrow"}, {models: Array(9).fill("model")}, {session_ids: [null]}]) assert.throws(() => decodeTranscript({...metadata,...change}, project));
});
