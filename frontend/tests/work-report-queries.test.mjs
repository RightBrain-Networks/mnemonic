import assert from "node:assert/strict";
import test from "node:test";
import { InfiniteQueryObserver, QueryObserver } from "@tanstack/react-query";
import { createDashboardQueryClient } from "../lib/query-client.ts";
import { invalidateConfirmedWorkMutation } from "../lib/work-mutation-queries.ts";
import { invalidateWorkDetailReads, workDetailKeys } from "../lib/work-detail-queries.ts";
import { workReportDetailOptions, workReportProvenanceOptions, workReportKeys, invalidateWorkReportReads } from "../lib/work-report-queries.ts";
import * as fixture from "./phase12-fixtures.mjs";

const { project, work, reportId, followWork, actor, timestamp, envelope } = fixture;
const movedProject = "99999999-9999-4999-8999-999999999999";
const firstCursor = Buffer.from("first cursor").toString("base64url");
const freshCursor = Buffer.from("fresh cursor").toString("base64url");
const link = (id) => ({
  id: `aaaaaaaa-aaaa-4aaa-8aaa-${String(id).padStart(12, "0")}`,
  project_id: project, report_id: reportId, created_sequence: String(id),
  source_work_item_id: work, follow_up_work_item_id: followWork, ...actor, created_at: timestamp
});
const page = (ids, cursor, sequence = "9") => ({
  project_id: movedProject, work_item_id: work, direction: "created",
  items: ids.map(link), as_of_sequence: sequence, has_more: cursor !== null, next_cursor: cursor
});
const report = () => ({ ...structuredClone(envelope),
  report: { ...structuredClone(envelope.report), authoring_prompt: "The exact instructions used at closeout." } });

test("work-view report provenance preserves exact cursors and rejects mixed, duplicate and reordered pages", async (t) => {
  const client = createDashboardQueryClient();
  let next = page([2], null, "10");
  const cursors = [];
  t.mock.method(globalThis, "fetch", async (url) => {
    const cursor = new URL(url, "http://mnemonic.test").searchParams.get("cursor");
    cursors.push(cursor);
    return Response.json(cursor === null ? page([1], firstCursor) : next);
  });
  const options = workReportProvenanceOptions(movedProject, work, "created");
  const observer = new InfiniteQueryObserver(client, options);
  const stop = observer.subscribe(() => {});
  t.after(() => { stop(); client.clear(); });
  await observer.refetch();
  const retained = observer.getCurrentResult().data;
  for (const invalid of [page([2], null, "10"), page([1], null), page([0], null), page([2], firstCursor)]) {
    next = invalid;
    await observer.fetchNextPage();
    assert.equal(observer.getCurrentResult().isFetchNextPageError, true);
    assert.equal(observer.getCurrentResult().data, retained);
  }
  next = page([2], null);
  await observer.fetchNextPage();
  assert.deepEqual(cursors, [null, firstCursor, firstCursor, firstCursor, firstCursor, firstCursor]);
  assert.deepEqual(observer.getCurrentResult().data.pages.flatMap((entry) => entry.items.map((row) => row.id)), [link(1).id, link(2).id]);
  assert.equal(observer.getCurrentResult().hasNextPage, false);
});

test("provenance refresh rebuilds loaded continuations from the new snapshot head", async (t) => {
  const client = createDashboardQueryClient();
  const cursors = [];
  let refreshed = false;
  t.mock.method(globalThis, "fetch", async (url) => {
    const cursor = new URL(url, "http://mnemonic.test").searchParams.get("cursor");
    cursors.push(cursor);
    return Response.json(refreshed
      ? cursor === null ? page([1, 2], freshCursor, "10") : page([3], null, "10")
      : cursor === null ? page([1], firstCursor) : page([2], null));
  });
  const observer = new InfiniteQueryObserver(client, workReportProvenanceOptions(movedProject, work, "created"));
  const stop = observer.subscribe(() => {});
  t.after(() => { stop(); client.clear(); });
  await observer.refetch();
  await observer.fetchNextPage();
  refreshed = true;
  await client.invalidateQueries({ queryKey: workDetailKeys.work(movedProject, work) });
  assert.deepEqual(cursors, [null, firstCursor, null, freshCursor]);
  assert.deepEqual(observer.getCurrentResult().data.pages.flatMap((entry) => entry.items.map((row) => row.id)), [link(1).id, link(2).id, link(3).id]);
});

test("originating reports keep full authority-bound snapshots through refresh failures and close cancels reads", async (t) => {
  const client = createDashboardQueryClient();
  const original = report();
  const fetch = t.mock.method(globalThis, "fetch", async () => Response.json(original));
  const selected = { projectId: project, id: reportId };
  const options = workReportDetailOptions(movedProject, followWork, selected);
  const observer = new QueryObserver(client, options);
  const stop = observer.subscribe(() => {});
  t.after(() => { stop(); client.clear(); });
  await observer.refetch();
  const retained = observer.getCurrentResult().data;
  assert.equal(fetch.mock.calls[0].arguments[0], `/api/mnemonic/projects/${project}/job-completion-reports/${reportId}`);
  assert.deepEqual(retained, original);
  fetch.mock.mockImplementation(async () => Response.json({ ...original, report: { ...original.report, project_id: movedProject } }));
  await observer.refetch();
  assert.equal(observer.getCurrentResult().isRefetchError, true);
  assert.equal(observer.getCurrentResult().data, retained);
  const refreshed = { ...original, source_work_state: { ...original.source_work_state, canonical_work_item_id: followWork, deleted: true } };
  fetch.mock.mockImplementation(async () => Response.json(refreshed));
  await observer.refetch();
  assert.deepEqual(observer.getCurrentResult().data.source_work_state, refreshed.source_work_state);
  assert.deepEqual(observer.getCurrentResult().data.report, original.report);
  const held = Promise.withResolvers();
  let signal;
  fetch.mock.mockImplementation((_url, init) => { signal = init.signal; return held.promise; });
  const pending = observer.refetch();
  observer.setOptions(workReportDetailOptions(movedProject, followWork, null));
  assert.equal(signal.aborted, true);
  assert.equal(observer.getCurrentResult().data, undefined);
  held.resolve(Response.json(original));
  await pending;
  assert.deepEqual(client.getQueryData(options.queryKey), refreshed);
});

test("report invalidation reaches a moved follow-up view through immutable authority without refreshing unrelated work", async (t) => {
  const client = createDashboardQueryClient();
  t.after(() => client.clear());
  const selected = workReportKeys.report(movedProject, followWork, project, reportId);
  const unrelated = workDetailKeys.context(movedProject, followWork);
  client.setQueryData(selected, report());
  client.setQueryData(unrelated, ["unrelated context"]);
  await invalidateConfirmedWorkMutation(client, {
    kind: "dismiss_job_completion_report",
    intent: { kind: "dismiss_job_completion_report", projectId: project, conflictKeys: [`report:${project}:${reportId}`] },
    result: { project_id: project, report_id: reportId }
  });
  assert.equal(client.getQueryState(selected).isInvalidated, true);
  assert.equal(client.getQueryState(unrelated).isInvalidated, false);
  client.setQueryData(selected, report());
  await invalidateWorkReportReads(client, project);
  assert.equal(client.getQueryState(selected).isInvalidated, true);
  assert.equal(client.getQueryState(unrelated).isInvalidated, false);
  client.setQueryData(selected, report());
  await invalidateWorkDetailReads(client, project);
  assert.equal(client.getQueryState(selected).isInvalidated, true);
  assert.equal(client.getQueryState(unrelated).isInvalidated, false);
});
