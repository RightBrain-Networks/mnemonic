import assert from "node:assert/strict";
import test from "node:test";
import {
  decodeProjectCatalogPage,
  loadCompleteProjectCatalog,
  preservedWorkMoveDisplayStatus,
  resolveCurrentWorkProject,
  sameProjectCatalog,
  summaryAfterWorkMove,
  workMoveDisabledReason
} from "../lib/work-move.ts";

const source = "11111111-1111-4111-8111-111111111111";
const target = "22222222-2222-4222-8222-222222222222";
const third = "33333333-3333-4333-8333-333333333333";
const work = "44444444-4444-4444-8444-444444444444";
const timestamp = "2026-09-06T12:00:00Z";

function project(id, name = `Project ${id.slice(-4)}`) {
  return {
    id,
    name,
    slug: `project-${id.slice(-4)}`,
    description: "",
    repository_url: null,
    created_at: timestamp,
    updated_at: timestamp
  };
}

function generatedProject(index) {
  const suffix = String(index + 10).padStart(12, "0");
  return project(
    `00000000-0000-4000-8000-${suffix}`,
    `Project ${String(110 - index).padStart(3, "0")}`
  );
}

function context(overrides = {}) {
  return {
    work_item: { id: work, project_id: source, status: "pending" },
    readiness: {
      has_active_lease: false,
      has_dropped_lease: false,
      is_gated: false,
      ...overrides.readiness
    },
    duplicate_member_total: 0,
    relationship_counts: { total: 0 },
    ...overrides
  };
}

test("move presentation retains the derived Dropped state", () => {
  assert.equal(
    preservedWorkMoveDisplayStatus(context({
      readiness: { has_active_lease: false, has_dropped_lease: true, is_gated: false }
    })),
    "dropped"
  );
  assert.equal(
    preservedWorkMoveDisplayStatus(context({
      work_item: { id: work, project_id: source, status: "deferred" }
    })),
    "deferred"
  );
});

test("move eligibility blocks every server guard, including canonical duplicate members", () => {
  assert.match(workMoveDisabledReason(null, false), /current work context/);
  assert.match(workMoveDisabledReason(context(), true), /pending mutation/);
  assert.match(workMoveDisabledReason(context({
    readiness: { has_active_lease: true, has_dropped_lease: false, is_gated: false }
  }), false), /active lease/);
  assert.match(workMoveDisabledReason(context({ duplicate_member_total: 1 }), false), /duplicate group/);
  assert.match(workMoveDisabledReason(context({
    relationship_counts: { total: 1 }
  }), false), /relationships/);
  assert.match(workMoveDisabledReason(context({
    readiness: { has_active_lease: false, has_dropped_lease: false, is_gated: true }
  }), false), /human question/);
  assert.equal(workMoveDisabledReason(context(), false), null);
});

test("a committed move immediately replaces the source-scoped summary with the target", () => {
  const previous = {
    work_item: { id: work, project_id: source, status: "pending" },
    readiness: { lifecycle_status: "pending", display_state: "dropped" },
    checkpoint_count: 3,
    ancestor_path: [],
    ancestor_path_truncated: false,
    current_context: null
  };
  const movedWork = { id: work, project_id: target, status: "pending", version: 2 };
  const result = {
    source_project_id: source,
    target_project_id: target,
    preserved_status: "pending",
    work_item: movedWork
  };
  const summary = summaryAfterWorkMove(previous, result, "dropped");
  assert.equal(summary.work_item, movedWork);
  assert.equal(summary.work_item.project_id, target);
  assert.equal(summary.readiness.display_state, "dropped");
  assert.equal(summary.readiness.lifecycle_status, "pending");
  assert.equal(summaryAfterWorkMove({
    ...previous,
    work_item: { ...previous.work_item, project_id: third }
  }, result, "pending"), null);
});

test("current placement lookup verifies the hint and probes every current project", async () => {
  const calls = [];
  const resolved = await resolveCurrentWorkProject(
    [{ id: source }, { id: target }, { id: third }],
    work,
    source,
    async (projectId, workItemId) => {
      calls.push([projectId, workItemId]);
      return projectId === target;
    }
  );
  assert.equal(resolved, target);
  assert.deepEqual(calls, [[source, work], [target, work]]);

  assert.equal(await resolveCurrentWorkProject(
    [{ id: source }, { id: target }],
    work,
    null,
    async () => false
  ), null);
  await assert.rejects(resolveCurrentWorkProject(
    [{ id: source }, { id: source.toUpperCase() }],
    work,
    source,
    async () => true
  ), /lookup scope is invalid/);
  await assert.rejects(resolveCurrentWorkProject(
    [{ id: source }],
    work,
    source,
    async () => { throw new Error("probe failed"); }
  ), /probe failed/);
});

test("project catalog pages are decoded exactly", () => {
  const item = {
    ...project(source),
    repository_url: "https://example.test/repository"
  };
  assert.deepEqual(decodeProjectCatalogPage({
    items: [item],
    total: 1,
    limit: 100,
    offset: 0
  }, 0), {
    items: [item],
    total: 1,
    limit: 100,
    offset: 0
  });

  assert.throws(() => decodeProjectCatalogPage({
    items: [{ ...item, unexpected: true }],
    total: 1,
    limit: 100,
    offset: 0
  }, 0), /catalog response was invalid/);
  assert.throws(() => decodeProjectCatalogPage({
    items: [{ ...item, repository_url: "https://user@example.test/repository" }],
    total: 1,
    limit: 100,
    offset: 0
  }, 0), /catalog response was invalid/);
  assert.throws(() => decodeProjectCatalogPage({
    items: [],
    total: 1,
    limit: 100,
    offset: 0
  }, 0), /catalog response was invalid/);
});

test("complete project catalog loading verifies every page and sorts deterministically", async () => {
  const catalog = Array.from({ length: 101 }, (_, index) => generatedProject(index));
  const calls = [];
  const loaded = await loadCompleteProjectCatalog(async (offset) => {
    calls.push(offset);
    return {
      items: catalog.slice(offset, offset + 100),
      total: catalog.length,
      limit: 100,
      offset
    };
  }, () => true);
  assert.deepEqual(calls, [0, 100]);
  assert.equal(loaded.length, 101);
  assert.equal(loaded[0].name, "Project 010");
  assert.equal(loaded.at(-1).name, "Project 110");
});

test("complete project catalog loading rejects changing, truncated, and duplicate pages", async () => {
  const catalog = Array.from({ length: 101 }, (_, index) => generatedProject(index));
  await assert.rejects(loadCompleteProjectCatalog(async (offset) => ({
    items: offset === 0 ? catalog.slice(0, 100) : [
      catalog[100],
      generatedProject(101)
    ],
    total: offset === 0 ? 101 : 102,
    limit: 100,
    offset
  }), () => true), /catalog changed/);

  await assert.rejects(loadCompleteProjectCatalog(async (offset) => ({
    items: offset === 0 ? catalog.slice(0, 100) : [],
    total: 101,
    limit: 100,
    offset
  }), () => true), /catalog response was invalid/);

  await assert.rejects(loadCompleteProjectCatalog(async (offset) => ({
    items: offset === 0 ? catalog.slice(0, 100) : [catalog[0]],
    total: 101,
    limit: 100,
    offset
  }), () => true), /catalog changed/);
});

test("complete project catalog loading discards a superseded page result", async () => {
  let current = true;
  const loaded = await loadCompleteProjectCatalog(async () => {
    current = false;
    return { items: [], total: 0, limit: 100, offset: 0 };
  }, () => current);
  assert.equal(loaded, null);
});

test("project catalog identity is order-independent and rejects invalid scopes", () => {
  assert.equal(
    sameProjectCatalog([project(source), project(target)], [project(target), project(source)]),
    true
  );
  assert.equal(
    sameProjectCatalog([project(source)], [project(target)]),
    false
  );
  assert.equal(
    sameProjectCatalog([project(source), project(source.toUpperCase())], [project(source)]),
    false
  );
});
