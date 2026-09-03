import assert from "node:assert/strict";
import test from "node:test";
import {
  decodeDuplicateCandidateSummary,
  decodeDuplicateSuggestionPage,
  duplicateSuggestionInputFromForm
} from "../lib/duplicate-suggestions.ts";

const canonicalId = "7a5dc555-0a6d-4f92-9678-1647524827c8";
const memberId = "f1cf3691-7d28-4716-94a9-4867b341a685";
const createdAt = "2026-09-02T15:04:05Z";

function request(overrides = {}) {
  return {
    title: "Durable work",
    summary: "Keep the durable objective coherent.",
    initial_prompt: "Exact creation context.",
    tags: ["phase-9"],
    exclude_work_item_id: null,
    limit: 5,
    ...overrides
  };
}

function candidate(overrides = {}) {
  return {
    work_item_id: canonicalId,
    title: "Durable work",
    summary: "Existing durable objective.",
    status: "done",
    updated_at: createdAt,
    duplicate_member_count: 0,
    ...overrides
  };
}

function suggestion(overrides = {}) {
  return {
    canonical_work: candidate(),
    matched_member: { id: canonicalId, title: "Durable work", status: "done" },
    rank: 1,
    signals: ["exact_title", "lexical"],
    ...overrides
  };
}

function page(overrides = {}) {
  return {
    items: [suggestion()],
    limit: 5,
    mode: "lexical",
    semantic_available: false,
    semantic_scope: "unavailable",
    composition_version: "duplicate-suggestion-v1",
    exact_title_group_total: 1,
    omitted_exact_title_group_count: 0,
    ...overrides
  };
}

test("the create-dialog snapshot is exact, normalized like creation, and deeply frozen", () => {
  const form = new FormData();
  form.set("title", "  Keep exact title  ");
  form.set("summary", "Summary kept exactly.");
  form.set("prompt", "Prompt kept exactly.\n");
  form.set("tags", " Phase-9, API, phase-9,  UI ");
  form.set("priority", "91");
  form.set("repository_branch", "never-forwarded");

  const input = duplicateSuggestionInputFromForm(form);
  assert.deepEqual(input, {
    title: "  Keep exact title  ",
    summary: "Summary kept exactly.",
    initial_prompt: "Prompt kept exactly.\n",
    tags: ["phase-9", "api", "ui"],
    exclude_work_item_id: null,
    limit: 5
  });
  assert.deepEqual(Object.keys(input), [
    "title", "summary", "initial_prompt", "tags", "exclude_work_item_id", "limit"
  ]);
  assert.equal(Object.isFrozen(input), true);
  assert.equal(Object.isFrozen(input.tags), true);
});

test("strict suggestion decoding accepts completed canonical work and alias evidence", () => {
  const input = request({ title: "  ＤＵＲＡＢＬＥ\tWork " });
  const value = page({
    mode: "hybrid_full",
    semantic_available: true,
    semantic_scope: "full_project",
    items: [suggestion({
      canonical_work: candidate({
        title: "Canonical objective",
        duplicate_member_count: 1
      }),
      matched_member: { id: memberId, title: "durable Work", status: "wont-do" },
      signals: ["exact_title", "lexical", "semantic"]
    })]
  });
  const decoded = decodeDuplicateSuggestionPage(value, input);
  assert.equal(decoded.items[0].canonical_work.status, "done");
  assert.equal(decoded.items[0].matched_member.id, memberId);
  assert.deepEqual(decoded.items[0].signals, ["exact_title", "lexical", "semantic"]);
  assert.deepEqual(decodeDuplicateCandidateSummary(candidate()), candidate());
});

test("suggestion decoder rejects private fields, bad identities, ranks, and signals", () => {
  const input = request();
  const invalidPages = [
    { ...page(), lease_token: "private" },
    page({ items: [{ ...suggestion(), raw_score: 0.99 }] }),
    page({ items: [suggestion({ canonical_work: { ...candidate(), readiness: {} } })] }),
    page({ items: [suggestion({ matched_member: { id: "bad", title: "Durable work", status: "done" } })] }),
    page({ items: [suggestion({ rank: 2 })] }),
    page({ items: [suggestion({ signals: [] })] }),
    page({ items: [suggestion({ signals: ["lexical", "exact_title"] })] }),
    page({ items: [suggestion({ signals: ["lexical", "lexical"] })] }),
    page({ items: [suggestion({ signals: ["probability"] })] }),
    page({ items: [suggestion({ signals: ["exact_title"], matched_member: {
      id: canonicalId, title: "Different title", status: "done"
    } })] }),
    page({ items: [suggestion({ canonical_work: candidate({ duplicate_member_count: 0 }), matched_member: {
      id: memberId, title: "Durable work", status: "done"
    } })] })
  ];
  for (const invalid of invalidPages) {
    assert.throws(() => decodeDuplicateSuggestionPage(invalid, input), /invalid|incoherent/);
  }
});

test("suggestion decoder enforces page mode, group, limit, and exact-lane coherence", () => {
  const secondId = "11111111-1111-4111-8111-111111111111";
  const input = request();
  const twoItems = [
    suggestion(),
    suggestion({
      canonical_work: candidate({ work_item_id: secondId, title: "Related work" }),
      matched_member: { id: secondId, title: "Related work", status: "done" },
      rank: 2,
      signals: ["lexical"]
    })
  ];
  for (const invalid of [
    page({ mode: "hybrid_full" }),
    page({ semantic_available: true }),
    page({ semantic_scope: "full_project" }),
    page({ composition_version: "future-version" }),
    page({ limit: 4 }),
    page({ items: [suggestion(), suggestion({ rank: 2 })] }),
    page({ items: twoItems, exact_title_group_total: 0 }),
    page({ items: twoItems.map((item, index) => index === 0
      ? { ...item, signals: ["lexical"] }
      : { ...item, signals: ["exact_title"] }) }),
    page({ items: [suggestion({ signals: ["semantic"] })] }),
    page({ exact_title_group_total: Number.POSITIVE_INFINITY }),
    page({ omitted_exact_title_group_count: 1 })
  ]) assert.throws(() => decodeDuplicateSuggestionPage(invalid, input), /invalid|incoherent/);

  const omitted = decodeDuplicateSuggestionPage(
    page({ limit: 1, exact_title_group_total: 3, omitted_exact_title_group_count: 2 }),
    request({ limit: 1 })
  );
  assert.equal(omitted.omitted_exact_title_group_count, 2);
  assert.throws(() => decodeDuplicateSuggestionPage(page(), request({
    exclude_work_item_id: canonicalId
  })), /incoherent/);
  assert.throws(() => decodeDuplicateSuggestionPage(page({
    items: [suggestion({
      canonical_work: candidate({
        title: "Canonical objective",
        duplicate_member_count: 1
      }),
      matched_member: { id: memberId, title: "Related alias", status: "done" },
      signals: ["lexical"]
    })],
    exact_title_group_total: 0
  }), request({ exclude_work_item_id: memberId })), /incoherent/);
});

test("exact-title decoding mirrors create-title Unicode boundary trimming", () => {
  for (const separator of ["\u2028", "\u0085"]) {
    assert.deepEqual(decodeDuplicateSuggestionPage(page(), request({
      title: `${separator}Durable work${separator}`
    })).items[0].signals, ["exact_title", "lexical"]);
    const storedTitle = `${separator}Durable work${separator}`;
    assert.throws(() => decodeDuplicateSuggestionPage(page({
      items: [suggestion({
        canonical_work: candidate({ title: storedTitle }),
        matched_member: { id: canonicalId, title: storedTitle, status: "done" },
        signals: ["exact_title"]
      })]
    }), request()), /incoherent/);
  }
  assert.throws(() => decodeDuplicateSuggestionPage(page(), request({
    title: "\ufeffDurable work\ufeff"
  })), /incoherent/);
});
