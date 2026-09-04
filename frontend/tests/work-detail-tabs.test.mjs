import assert from "node:assert/strict";
import test from "node:test";
import {
  DETAIL_TABS,
  copyKey,
  detailTabLabels,
  detailTabs
} from "../lib/work-detail-tabs.ts";

function summary(overrides = {}) {
  return {
    work_item: { id: "work-1" },
    checkpoint_count: 3,
    readiness: { unresolved_gate_count: 0 },
    ...overrides
  };
}

function context(overrides = {}) {
  return {
    checkpoint_total: 7,
    relationship_counts: { incoming: 1, outgoing: 2, undirected: 1, total: 4 },
    unresolved_gate_total: 0,
    event_total: 12,
    ...overrides
  };
}

test("the six tabs keep their order and labels", () => {
  assert.deepEqual(DETAIL_TABS, [
    "context", "history", "evidence", "graph", "questions", "activity"
  ]);
  assert.deepEqual(detailTabs(null, summary()).map((tab) => tab.key), DETAIL_TABS);
  assert.deepEqual(detailTabs(null, summary()).map((tab) => tab.label), [
    "Context",
    "History",
    "Evidence",
    "Graph",
    "Questions",
    "Activity"
  ]);
  for (const key of DETAIL_TABS) assert.equal(typeof detailTabLabels[key], "string");
});

test("before the context loads counts fall back to the summary and unknown ones are omitted", () => {
  const tabs = detailTabs(null, summary({ checkpoint_count: 3 }));
  const byKey = Object.fromEntries(tabs.map((tab) => [tab.key, tab]));
  assert.deepEqual(byKey.context, { key: "context", label: "Context", alert: false });
  assert.deepEqual(byKey.history, { key: "history", label: "History", count: 3, alert: false });
  assert.deepEqual(byKey.evidence, { key: "evidence", label: "Evidence", alert: false });
  assert.equal("count" in byKey.evidence, false);
  assert.deepEqual(byKey.graph, { key: "graph", label: "Graph", alert: false });
  assert.equal("count" in byKey.graph, false);
  assert.deepEqual(byKey.questions, { key: "questions", label: "Questions", count: 0, alert: false });
  assert.deepEqual(byKey.activity, { key: "activity", label: "Activity", alert: false });
  assert.equal("count" in byKey.activity, false);
});

test("a loaded context supplies every count and overrides the summary", () => {
  const tabs = detailTabs(context(), summary({ checkpoint_count: 1, readiness: { unresolved_gate_count: 5 } }));
  const byKey = Object.fromEntries(tabs.map((tab) => [tab.key, tab]));
  assert.equal("count" in byKey.context, false);
  assert.equal(byKey.history.count, 7);
  assert.equal("count" in byKey.evidence, false);
  assert.equal(byKey.graph.count, 4);
  assert.equal(byKey.questions.count, 0);
  assert.equal(byKey.questions.alert, false);
  assert.equal(byKey.activity.count, 12);
});

test("questions alert whenever unresolved human gates exist, from either source", () => {
  const fromSummary = detailTabs(null, summary({ readiness: { unresolved_gate_count: 2 } }));
  const summaryQuestions = fromSummary.find((tab) => tab.key === "questions");
  assert.deepEqual(summaryQuestions, { key: "questions", label: "Questions", count: 2, alert: true });
  assert.ok(fromSummary.filter((tab) => tab.alert).every((tab) => tab.key === "questions"));

  const fromContext = detailTabs(
    context({ unresolved_gate_total: 1 }),
    summary({ readiness: { unresolved_gate_count: 0 } })
  );
  const contextQuestions = fromContext.find((tab) => tab.key === "questions");
  assert.deepEqual(contextQuestions, { key: "questions", label: "Questions", count: 1, alert: true });

  const zeroContext = detailTabs(
    context({ unresolved_gate_total: 0 }),
    summary({ readiness: { unresolved_gate_count: 3 } })
  );
  assert.equal(zeroContext.find((tab) => tab.key === "questions").alert, false);
});

test("copy keys are namespaced by work item id and kind", () => {
  assert.equal(copyKey("work-1", "id"), "work-1:id");
  assert.equal(copyKey("work-1", "pointer"), "work-1:pointer");
  assert.equal(copyKey("work-1", "context"), "work-1:context");
  assert.equal(copyKey("work-1", "audit-id"), "work-1:audit-id");
  assert.equal(copyKey("work-1", "canonical-id"), "work-1:canonical-id");
  assert.notEqual(copyKey("work-1", "id"), copyKey("work-2", "id"));
});
