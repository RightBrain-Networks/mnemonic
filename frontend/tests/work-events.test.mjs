import assert from "node:assert/strict";
import test from "node:test";
import {
  dashboardMutationActor,
  decodeWorkEvent,
  decodeWorkEventForWork,
  decodeWorkEventPage,
  progressEventInput,
  relationshipEventDescription,
  resetNewestEventOffset,
  safeEventBody,
  WORK_EVENT_TYPES,
  workEventActorLabel,
  workEventSearchParams,
  workEventTitle
} from "../lib/work-events.ts";

const project = "e36a7e53-938f-4c8a-b75a-af9c7331711a";
const work = "7a5dc555-0a6d-4f92-9678-1647524827c8";
const counterpart = "f1cf3691-7d28-4716-94a9-4867b341a685";

function event(overrides = {}) {
  return {
    id: 1,
    project_id: project,
    work_item_id: work,
    event_type: "progress",
    actor_kind: "client",
    actor_client: "dashboard",
    actor_session_id: "tab-session",
    actor_model: null,
    body: "Kept exactly.",
    checkpoint_id: null,
    lease_generation_id: null,
    lease_release_id: null,
    relationship_id: null,
    relationship_source_work_item_id: null,
    relationship_target_work_item_id: null,
    relationship_context_checkpoint_work_item_id: null,
    relationship_context_checkpoint_id: null,
    relationship_direction: null,
    counterpart_work_item_id: null,
    metadata_version: 1,
    metadata: {},
    origin: "live",
    created_at: "2026-09-01T12:00:00Z",
    ...overrides
  };
}

test("every Phase 5 event type has a deterministic human label", () => {
  assert.equal(WORK_EVENT_TYPES.length, 14);
  assert.deepEqual(
    WORK_EVENT_TYPES.map(workEventTitle),
    [
      "Created work",
      "Updated work",
      "Changed status",
      "Reopened work",
      "Claimed work",
      "Released claim",
      "Added checkpoint",
      "Progress update",
      "Added dependency",
      "Removed dependency",
      "Added relationship",
      "Removed relationship",
      "Completed work",
      "Deleted work"
    ]
  );
});

test("event queries are newest-first, bounded, filtered, and reset after invalidation", () => {
  assert.equal(
    workEventSearchParams({ eventType: "progress", limit: 20, offset: 40 }).toString(),
    "order=newest&limit=20&offset=40&event_type=progress"
  );
  assert.equal(workEventSearchParams({}).toString(), "order=newest&limit=20&offset=0");
  assert.equal(resetNewestEventOffset(), 0);
});

test("dashboard actor and progress requests use the exact stable nested names", () => {
  assert.deepEqual(dashboardMutationActor("per-tab-id"), {
    actor_client: "dashboard",
    actor_session_id: "per-tab-id"
  });
  assert.deepEqual(progressEventInput("Exact progress", "per-tab-id"), {
    event_type: "progress",
    body: "Exact progress",
    metadata: {},
    actor: { actor_client: "dashboard", actor_session_id: "per-tab-id" }
  });
});

test("append decoding accepts only the requested project and work endpoint", () => {
  let successfulDecodes = 0;
  const decodeSuccessfulAppend = (value) => {
    const decoded = decodeWorkEventForWork(value, project, work);
    successfulDecodes += 1;
    return decoded;
  };

  assert.equal(decodeSuccessfulAppend(event()).body, "Kept exactly.");
  assert.equal(successfulDecodes, 1);
  assert.throws(
    () => decodeSuccessfulAppend(event({ project_id: counterpart })),
    /invalid work-event response/
  );
  assert.throws(
    () => decodeSuccessfulAppend(event({ work_item_id: counterpart })),
    /invalid work-event response/
  );
  assert.equal(successfulDecodes, 1);
});

test("relationship labels preserve endpoint-relative direction and safe counterpart fallback", () => {
  const base = {
    event_type: "relationship_added",
    body: null,
    relationship_id: "26a3a437-0af3-405a-ab82-7932d17869e0",
    relationship_source_work_item_id: work,
    relationship_target_work_item_id: counterpart,
    counterpart_work_item_id: counterpart,
    metadata: { relationship_type: "discovered-from" }
  };
  assert.equal(
    relationshipEventDescription(event({ ...base, relationship_direction: "outgoing" }), "Investigation"),
    "Added a link showing this work was discovered from “Investigation”."
  );
  assert.equal(
    relationshipEventDescription(event({ ...base, relationship_direction: "incoming" }), "Follow-up"),
    "Added a link showing “Follow-up” was discovered from this work."
  );
  assert.equal(
    relationshipEventDescription(event({
      ...base,
      relationship_direction: "undirected",
      metadata: { relationship_type: "related" }
    })),
    `Added a related-work link with work ${counterpart}.`
  );
});

test("hostile progress remains exact text and actor fallbacks never invent provenance", () => {
  const hostile = '<img src=x onerror="globalThis.pwned=true"><script>alert(1)</script>';
  const decoded = decodeWorkEvent(event({ body: hostile }));
  assert.equal(safeEventBody(decoded), hostile);
  assert.equal(workEventActorLabel(decoded), "dashboard · tab-session");
  assert.equal(workEventActorLabel(decodeWorkEvent(event({
    event_type: "work_updated",
    actor_kind: "unattributed",
    actor_client: null,
    actor_session_id: null,
    actor_model: null,
    body: null,
    metadata: {
      changes: { title: { before: "Earlier", after: "Current" } },
      work_version: 2
    }
  }))), "Unattributed earlier action");
});

test("strict decoders reject widened event/page responses and malformed metadata", () => {
  const rejects = (overrides) => assert.throws(
    () => decodeWorkEvent(event(overrides)),
    /invalid work-event response/
  );
  rejects({ unexpected: "field" });
  rejects({ project_id: "not-a-uuid" });
  rejects({ created_at: "2026-02-30T12:00:00Z" });
  rejects({ created_at: "2026-09-01T12:00:00+00:00" });
  rejects({ actor_model: " " });
  rejects({ actor_client: "x".repeat(81) });
  rejects({ body: "\t\n" });
  rejects({ body: `valid\0invalid` });
  rejects({ body: "x".repeat(4001) });
  rejects({ metadata: { nested: { Authorization: "forbidden-key" } } });
  rejects({ metadata: { note: "x".repeat(16_384) } });
  rejects({
    event_type: "checkpoint_added",
    body: null,
    checkpoint_id: counterpart,
    metadata: { checkpoint_kind: "completion" }
  });
  rejects({ origin: "backfill" });
  rejects({
    actor_kind: "unattributed",
    actor_client: null,
    actor_session_id: null,
    actor_model: null
  });
  rejects({
    event_type: "work_created",
    body: null,
    checkpoint_id: counterpart,
    metadata: {
      initial: {
        title: "x".repeat(201),
        summary: "Summary",
        status: "open",
        priority: 50,
        version: 1
      }
    }
  });
  rejects({
    event_type: "work_claimed",
    body: null,
    lease_generation_id: counterpart,
    metadata: { expires_at: "2026-09-01T12:00:00+00:00" }
  });
  rejects({
    event_type: "work_released",
    body: null,
    lease_generation_id: counterpart,
    lease_release_id: "26a3a437-0af3-405a-ab82-7932d17869e0",
    metadata: {
      lease_holder_kind: "client",
      lease_holder_client: " ",
      lease_holder_session_id: "session"
    }
  });
  rejects({
    event_type: "relationship_added",
    body: null,
    relationship_id: "26a3a437-0af3-405a-ab82-7932d17869e0",
    relationship_source_work_item_id: work,
    relationship_target_work_item_id: counterpart,
    relationship_direction: "outgoing",
    counterpart_work_item_id: counterpart,
    metadata: { relationship_type: "related" }
  });

  assert.deepEqual(decodeWorkEventPage({
    items: [event()],
    total: 1,
    limit: 20,
    offset: 0,
    pre_phase5_history_may_be_incomplete: true
  }, project, work).pre_phase5_history_may_be_incomplete, true);
  for (const page of [
    {
      items: [], total: 0, limit: 20, offset: 0,
      pre_phase5_history_may_be_incomplete: false, leaked: "field"
    },
    {
      items: [], total: 0, limit: 101, offset: 0,
      pre_phase5_history_may_be_incomplete: false
    },
    {
      items: [event()], total: 0, limit: 20, offset: 0,
      pre_phase5_history_may_be_incomplete: false
    }
  ]) {
    assert.throws(() => decodeWorkEventPage(page, project, work), /invalid work-event page/);
  }
});

test("strict decoders reject cross-work pages and invalid relationship reference semantics", () => {
  assert.throws(() => decodeWorkEventPage({
    items: [event({ work_item_id: counterpart })],
    total: 1,
    limit: 20,
    offset: 0,
    pre_phase5_history_may_be_incomplete: false
  }, project, work), /invalid work-event page/);

  const relationship = {
    event_type: "relationship_added",
    body: null,
    relationship_id: "26a3a437-0af3-405a-ab82-7932d17869e0",
    relationship_source_work_item_id: work,
    relationship_target_work_item_id: counterpart,
    relationship_direction: "outgoing",
    counterpart_work_item_id: counterpart,
    metadata: { relationship_type: "discovered-from" }
  };
  for (const invalid of [
    relationship,
    {
      ...relationship,
      relationship_context_checkpoint_work_item_id: relationship.relationship_id,
      relationship_context_checkpoint_id: relationship.relationship_id
    },
    {
      ...relationship,
      relationship_context_checkpoint_work_item_id: work,
      relationship_context_checkpoint_id: relationship.relationship_id
    },
    {
      ...relationship,
      work_item_id: counterpart,
      relationship_source_work_item_id: counterpart,
      relationship_target_work_item_id: work,
      relationship_context_checkpoint_work_item_id: null,
      relationship_context_checkpoint_id: null,
      relationship_direction: "undirected",
      counterpart_work_item_id: work,
      metadata: { relationship_type: "related" }
    }
  ]) {
    assert.throws(() => decodeWorkEvent(event(invalid)), /invalid work-event response/);
  }

  assert.equal(decodeWorkEvent(event({
    ...relationship,
    relationship_context_checkpoint_work_item_id: counterpart,
    relationship_context_checkpoint_id: relationship.relationship_id
  })).relationship_context_checkpoint_work_item_id, counterpart);
});
