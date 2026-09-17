import assert from "node:assert/strict";
import test from "node:test";
import { decodeWorkTranscripts } from "../lib/work-transcripts.ts";
const project = "10000000-0000-4000-8000-000000000001";
const work = "20000000-0000-4000-8000-000000000002";
const id = "30000000-0000-4000-8000-000000000003";
const link = { id, project_id: project, work_item_id: work, filename: "session.jsonl", client: "codex", kind: "primary", status: "ready", last_updated_at: "2026-02-01T14:30:00Z", session_ids: ["native"], models: ["model"] };
test("work transcript references enforce identities and disclose omitted links", () => {
  const page = {items:[link],total:1,omitted_count:0};
  assert.equal(decodeWorkTranscripts(page,project,work).items[0].id,id);
  for (const change of [{items:[{...link,project_id:work}]},{items:[{...link,work_item_id:id}]},{items:[{...link,text:"unexpected body"}]},{total:2},{items:[link,link],total:2}]) assert.throws(()=>decodeWorkTranscripts({...page,...change},project,work));
});
