import assert from "node:assert/strict";
import { setImmediate } from "node:timers/promises";
import test from "node:test";
import { CancelledError, QueryObserver } from "@tanstack/react-query";
import { createDashboardQueryClient } from "../lib/query-client.ts";
import {
  fetchFreshWorkContext, fetchFreshWorkSettings, workContextOptions, workSettingsOptions
} from "../lib/work-detail-queries.ts";

const project = "11111111-1111-4111-8111-111111111111";
const work = "22222222-2222-4222-8222-222222222222";

for (const [name, optionsFor, fetchFresh] of [
  ["context", workContextOptions, fetchFreshWorkContext],
  ["policy", workSettingsOptions, fetchFreshWorkSettings]
]) {
  test(`cancelled fresh ${name} reads reject retained cache after a disabled observer detaches`, async (t) => {
    const client = createDashboardQueryClient();
    const options = optionsFor(project, work);
    const retained = { retained: name, work_item: { id: work, project_id: project } };
    client.setQueryData(options.queryKey, retained);
    const observer = new QueryObserver(client, { ...options, enabled: false });
    const stop = observer.subscribe(() => {});
    t.after(() => { stop(); client.clear(); });
    let signal;
    const held = Promise.withResolvers();
    t.mock.method(globalThis, "fetch", (_url, init) => { signal = init.signal; return held.promise; });
    const pending = fetchFresh(client, project, work);
    const rejected = assert.rejects(pending, CancelledError);
    await setImmediate();
    assert.equal(signal.aborted, false);
    observer.setOptions({ queryKey: ["mnemonic", "disabled", name], enabled: false });
    assert.equal(signal.aborted, true);
    await rejected;
    assert.equal(client.getQueryData(options.queryKey), retained,
      "read-only display cache survives without becoming fresh verification");
    held.resolve(Response.json({ malformed: "late response must be ignored" }));
    await setImmediate();
    assert.equal(client.getQueryData(options.queryKey), retained);
  });

  test(`fresh ${name} helper rejects a cached result when its own transport never ran`, async (t) => {
    const client = createDashboardQueryClient();
    t.after(() => client.clear());
    t.mock.method(client, "fetchQuery", async () => ({ retained: name }));
    await assert.rejects(fetchFresh(client, project, work), CancelledError);
  });
}
