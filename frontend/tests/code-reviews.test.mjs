import assert from "node:assert/strict";
import test from "node:test";
import {
  codeReviewDecision,
  reviewThresholdLabel,
  reviewThresholdMatches,
  validReviewThreshold,
} from "../lib/code-review-policy.ts";
import {
  validateColdReviewPointer,
} from "../lib/code-review-prompts.ts";
import {
  validReviewHandoff,
  validRepositoryRange,
  validFollowUpAnswer,
  decodeReviewPolicy,
  decodeCodeReviewDetail,
  decodeCodeReviewContext,
  decodeWorkFollowUpDetail,
  decodeFollowUpAnswerResult,
  decodeReviewQueuePage,
} from "../lib/code-reviews.ts";
import {
  allowedQueryKeys,
  invalidMutationBody,
  phase12ResponseLimitBytes,
} from "../lib/proxy-policy.ts";
import { classifyMutationResponse } from "../lib/mutation-responses.ts";
import { decodeProjectSettings } from "../lib/job-completion-reports.ts";
import { decodeReadiness } from "../lib/readiness-codecs.ts";
import {
  currentManualStatusAction,
  statusActionDisabledReason,
} from "../lib/work-status-actions.ts";
import * as f from "./phase12-fixtures.mjs";
import * as r from "./code-review-fixtures.mjs";

test("To review accepts completed implementation and rejects other lifecycle states", () => {
  const value = {
    lifecycle_status: "done", is_terminal: true, is_duplicate: false,
    canonical_work_item_id: f.work, has_active_lease: false, has_dropped_lease: false,
    active_lease: null, unresolved_blocker_count: 0, is_blocked: false,
    unresolved_gate_count: 0, is_gated: false, is_ready: false, display_state: "to-review"
  };
  assert.equal(decodeReadiness(value, "done", f.work).display_state, "to-review");
  for (const status of ["pending", "deferred", "wont-do", "promoted"]) {
    assert.throws(() => decodeReadiness({ ...value, lifecycle_status: status }, status, f.work));
  }
  assert.throws(() => decodeReadiness({ ...value, is_duplicate: true, canonical_work_item_id: f.followWork }, "done", f.work));
});

test("all priority/threshold/toggle/depth combinations obey sentinel, inclusive and structural precedence", () => {
  for (let required = 0; required <= 100; required += 5)
    for (let optional = 0; optional <= 100; optional += 5)
      for (let priority = 0; priority <= 100; priority++)
        for (const allow of [false, true])
          for (const depth of [0, 1, 2]) {
            const match = (threshold) =>
              threshold === 0 || (threshold < 100 && priority >= threshold);
            const expected =
              depth === 2
                ? "ineligible_depth_limit"
                : depth === 1 && !allow
                  ? "ineligible_remediation_disabled"
                  : match(required)
                    ? "mandatory"
                    : match(optional)
                      ? "ask_recommendation"
                      : "not_requested";
            assert.equal(
              codeReviewDecision(
                {
                  code_review_required_min_priority: required,
                  code_review_optional_min_priority: optional,
                  allow_remediation_code_reviews: allow,
                },
                priority,
                depth,
              ),
              expected,
            );
          }
  assert.equal(reviewThresholdLabel(0), "Always");
  assert.equal(reviewThresholdLabel(100), "Never");
  assert.equal(reviewThresholdMatches(100, 100), false);
  for (const bad of [false, true, null, "5", 5.5, 1, -5, 105, NaN, Infinity])
    assert.equal(validReviewThreshold(bad), false);
});

test("settings parse exact strict thresholds and independent fields", () => {
  const settings = {
    lease_default_minutes: 15,
    lease_minimum_minutes: 10,
    lease_maximum_minutes: 120,
    project_id: f.project,
    revision: "3",
    recall_pointer_template: null,
    job_completion_report_prompt: "Write clearly.",
    code_review_required_min_priority: 100,
    code_review_optional_min_priority: 0,
    allow_remediation_code_reviews: false,
  };
  assert.deepEqual(decodeProjectSettings(settings, f.project), settings);
  for (const change of [
    { code_review_required_min_priority: true },
    { code_review_optional_min_priority: 99 },
    { allow_remediation_code_reviews: 0 },
    { code_review_required_min_priority: undefined },
  ])
    assert.throws(() =>
      decodeProjectSettings({ ...settings, ...change }, f.project),
    );
  for (const change of [
    { code_review_required_min_priority: 0 },
    { code_review_optional_min_priority: 95 },
    { allow_remediation_code_reviews: true },
  ])
    assert.equal(
      invalidMutationBody(`projects/${f.project}/settings`, "PATCH", {
        expected_revision: "3",
        ...change,
      }),
      null,
    );
  assert.throws(() =>
    decodeReviewPolicy(
      { ...r.policy, decision: "not_requested" },
      f.project,
      f.work,
    ),
  );
});

test("handoff enforces immutable complete scope, Unicode and independent byte limits without mutation", () => {
  assert.ok(validReviewHandoff(r.handoff));
  const original = JSON.stringify(r.handoff);
  validReviewHandoff(r.handoff);
  assert.equal(JSON.stringify(r.handoff), original);
  const repo = r.handoff.scope.repositories[0];
  for (const changed of [
    { base_commit: "HEAD" },
    { head_commit: "A".repeat(40) },
    { object_format: "sha256" },
    { repository_url: "https://user:secret@example.com/repo" },
    { repository_url: "https://example.com/repo?token=secret" },
    { checkout_path: "/tmp/$(bad)" },
    { checkout_path: "relative/path" },
    { secret: "no" },
  ])
    assert.equal(validRepositoryRange({ ...repo, ...changed }), false);
  assert.equal(
    validReviewHandoff({ ...r.handoff, scope: { repositories: [repo, repo] } }),
    false,
  );
  assert.equal(
    validReviewHandoff({
      ...r.handoff,
      handoff: { ...r.handoff.handoff, decisions: ["x\u202e"] },
    }),
    false,
  );
  assert.equal(
    validReviewHandoff({
      ...r.handoff,
      handoff: {
        ...r.handoff.handoff,
        decisions: Array(20).fill("🙂".repeat(2000)),
      },
    }),
    false,
  );
  assert.ok(
    validFollowUpAnswer({
      kind: "code_review_recommendation",
      recommend_review: false,
      rationale: "Trivial change.",
    }),
  );
  assert.equal(
    validFollowUpAnswer({
      kind: "code_review_recommendation",
      recommend_review: false,
      rationale: "Trivial change.",
      code_review_handoff: r.handoff,
    }),
    false,
  );
  assert.equal(
    validFollowUpAnswer({
      kind: "code_review_recommendation",
      recommend_review: true,
      rationale: "Complex change.",
    }),
    false,
  );
});

test("cold prompt routing requires exact valid review identity and pinned repository scope", () => {
  const pointer = {
    project_id: f.project,
    work_item_id: f.work,
    code_review_id: r.reviewId,
    review_version: 1,
    scope_sha256: r.review.scope_sha256,
    scope: r.handoff.scope,
  };
  assert.doesNotThrow(() => validateColdReviewPointer(pointer));
  for (const override of [
    { project_id: "wrong" }, { work_item_id: "wrong" }, { code_review_id: "wrong" },
    { review_version: 0 }, { scope_sha256: "wrong" }, { scope: { repositories: [] } }
  ]) assert.throws(() => validateColdReviewPointer({ ...pointer, ...override }));
});

test("durable negative answers survive source deletion and review detail never guesses cross-linked entities", () => {
  for (const title of [
    "Existing work\nwith a retained second line",
    "Existing work\twith a retained tab",
  ]) {
    const source = { ...r.source, title };
    assert.equal(
      decodeCodeReviewDetail(
        { ...r.reviewDetail, source_work_state: source },
        f.project,
        f.work,
        r.reviewId,
      ).source_work_state.title,
      title,
    );
    assert.equal(
      decodeWorkFollowUpDetail(
        { ...r.negativeDetail, source_work_state: source },
        f.project,
        f.work,
        r.followId,
      ).source_work_state.title,
      title,
    );
    assert.equal(
      decodeReviewQueuePage(
        { ...r.queuePage, items: [{ ...r.queueRow, title }] },
        f.project,
        "reviews",
      ).items[0].title,
      title,
    );
  }
  assert.deepEqual(
    decodeCodeReviewDetail(r.reviewDetail, f.project, f.work, r.reviewId),
    r.reviewDetail,
  );
  for (const bad of [
    { completion_event_id: "9223372036854775808" },
    { created_sequence: "9999999999999999999" },
    { created_event_id: "01" },
    { created_sequence: "0" },
  ])
    assert.throws(() =>
      decodeCodeReviewDetail(
        { ...r.reviewDetail, review: { ...r.review, ...bad } },
        f.project,
        f.work,
        r.reviewId,
      ),
    );
  assert.throws(() =>
    decodeWorkFollowUpDetail(
      {
        ...r.negativeDetail,
        follow_up: {
          ...r.negativeDetail.follow_up,
          required_answer_fields: [],
        },
      },
      f.project,
      f.work,
      r.followId,
    ),
  );
  assert.equal(
    decodeWorkFollowUpDetail(
      {
        ...r.negativeDetail,
        source_work_state: { ...r.source, deleted: true, status: "pending" },
      },
      f.project,
      f.work,
      r.followId,
    ).answer.rationale,
    r.answer.rationale,
  );
  for (const bad of [
    { answer: null },
    { answer: { ...r.answer, follow_up_id: f.followWork } },
    { code_review: r.review },
    { extra: "secret" },
  ])
    assert.throws(() =>
      decodeWorkFollowUpDetail(
        { ...r.negativeDetail, ...bad },
        f.project,
        f.work,
        r.followId,
      ),
    );
  assert.throws(() =>
    decodeCodeReviewDetail(
      { ...r.reviewDetail, policy_decision: { ...r.policy, id: f.followWork } },
      f.project,
      f.work,
      r.reviewId,
    ),
  );
  assert.throws(() =>
    decodeCodeReviewContext(
      {
        remediation_depth: 0,
        current_review: r.review,
        pending_follow_up: r.question,
        remediation_origin: null,
      },
      f.project,
      f.work,
    ),
  );
  assert.throws(() =>
    decodeReviewQueuePage(
      { ...r.queuePage, items: [r.queueRow, r.queueRow] },
      f.project,
      "reviews",
    ),
  );
  assert.throws(() =>
    decodeReviewQueuePage(
      {
        ...r.queuePage,
        items: [
          {
            ...r.queueRow,
            lease: {
              holder_client: "reviewer",
              holder_session_id: "review",
              acquired_at: f.timestamp,
              renewed_at: f.timestamp,
              expires_at: f.timestamp,
              mode: "cold",
            },
          },
        ],
      },
      f.project,
      "reviews",
    ),
  );
});

function answerRequest(yes = false) {
  return {
    kind: "respond_to_work_follow_up",
    method: "POST",
    path: `/projects/${f.project}/work-items/${f.work}/agent-follow-ups/${r.followId}/answer`,
    operationId: f.operation,
    body: JSON.stringify({
      expected_follow_up_version: 1,
      actor: f.actor,
      client_operation_id: f.operation,
      answer: {
        kind: "code_review_recommendation",
        recommend_review: yes,
        rationale: r.answer.rationale,
        ...(yes ? { code_review_handoff: r.handoff } : {}),
      },
    }),
  };
}
test("answer receipts prove exact author/answer/version/scope and retain malformed outcomes for identical retries", async () => {
  for (const yes of [false, true]) {
    const req = answerRequest(yes),
      result = yes ? r.affirmativeResponse : r.negativeResponse;
    assert.equal(
      (await classifyMutationResponse(req, Response.json(result))).type,
      "success",
    );
    for (const bad of [
      { answer: { ...result.answer, rationale: "Different" } },
      { answer: { ...result.answer, actor_session_id: "imposter" } },
      { follow_up: { ...result.follow_up, version: 3 } },
    ])
      assert.equal(
        (
          await classifyMutationResponse(
            req,
            Response.json({ ...result, ...bad }),
          )
        ).type,
        "unresolved",
      );
  }
  const changed = {
    ...r.affirmativeResponse,
    code_review_handoff: {
      ...r.handoff,
      handoff: { ...r.handoff.handoff, change_summary: "Different" },
    },
  };
  assert.equal(
    (
      await classifyMutationResponse(
        answerRequest(true),
        Response.json(changed),
      )
    ).type,
    "unresolved",
  );
  const input = JSON.parse(answerRequest(false).body).answer;
  assert.deepEqual(
    decodeFollowUpAnswerResult(
      r.negativeResponse,
      f.project,
      f.work,
      r.followId,
      1,
      input,
      f.actor,
    ),
    r.negativeResponse,
  );
});

test("mandatory closeout receipt binds policy checkpoint, saved report revision and exact accepted handoff", async () => {
  const req = {
    kind: "complete_work",
    method: "POST",
    path: `/projects/${f.project}/work-items/${f.work}/complete`,
    operationId: f.operation,
    body: JSON.stringify({
      expected_version: 1,
      client_operation_id: f.operation,
      checkpoint: f.checkpointInput,
      job_completion_report: f.reportInput,
      code_review_handoff: r.handoff,
    }),
  };
  const result = {
    work_item: f.workItem,
    checkpoint: f.checkpoint,
    job_completion_report: f.report,
    review_policy_decision: r.policy,
    code_review_request: r.review,
    code_review_handoff: r.handoff,
  };
  assert.equal(
    (await classifyMutationResponse(req, Response.json(result))).type,
    "success",
  );
  for (const change of [
    { code_review_handoff: undefined },
    { code_review_request: { ...r.review, policy_decision_id: f.followWork } },
    { review_policy_decision: { ...r.policy, settings_revision: "4" } },
    { agent_follow_ups: [r.question] },
  ])
    assert.equal(
      (
        await classifyMutationResponse(
          req,
          Response.json({ ...result, ...change }),
        )
      ).type,
      "unresolved",
    );
});

test("proxy exposes bounded review reads and one answer write, never review lease or completion capability", () => {
  const base = `projects/${f.project}/work-items/${f.work}`;
  const answer = `${base}/agent-follow-ups/${r.followId}/answer`;
  assert.equal(
    invalidMutationBody(answer, "POST", JSON.parse(answerRequest(true).body)),
    null,
  );
  assert.equal(
    allowedQueryKeys(`${base}/code-reviews/${r.reviewId}/complete`, "POST"),
    null,
  );
  assert.equal(allowedQueryKeys(`${base}/claim`, "POST"), null);
  assert.equal(
    phase12ResponseLimitBytes(`${base}/code-reviews/${r.reviewId}`),
    786432,
  );
  assert.equal(
    phase12ResponseLimitBytes(`${base}/agent-follow-ups/${r.followId}`),
    65536,
  );
  assert.equal(phase12ResponseLimitBytes(answer), 1048576);
  assert.match(
    invalidMutationBody(answer, "POST", {
      ...JSON.parse(answerRequest().body),
      lease_token: "secret",
    }),
    /unsupported field/,
  );
  assert.equal(
    invalidMutationBody(base, "PATCH", {
      expected_version: 2,
      status: "pending",
      actor: f.actor,
      client_operation_id: f.operation,
      supersede_code_review_id: r.reviewId,
      expected_code_review_version: 1,
    }),
    null,
  );
});

test("review leases never offer manual Active or Pending actions", () => {
  const readiness = {
    lifecycle_status: "done",
    has_active_lease: true,
    active_lease: { purpose: "code_review" },
    is_gated: false,
    is_duplicate: false,
  };
  assert.equal(currentManualStatusAction("done", readiness), "done");
  assert.equal(statusActionDisabledReason("done", readiness, true), null);
});
