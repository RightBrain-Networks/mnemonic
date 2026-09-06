import type { LeasePublic, MutationActor, WorkStatus } from "./types.ts";
import {
  codeReviewDecision,
  validReviewThreshold,
  type CodeReviewDecision,
} from "./code-review-policy.ts";
import {
  boundedText,
  exactKeys,
  finiteInteger,
  jsonEqual,
  objectValue,
  sameUuid,
  validUnicode,
  validUtcDateTime,
  validUuid,
  type JsonObject,
} from "./wire-guards.ts";

export interface RepositoryRange {
  repository_key: string;
  repository_url?: string;
  checkout_path?: string;
  object_format: "sha1" | "sha256";
  base_commit: string;
  head_commit: string;
}
export interface CodeReviewScope {
  repositories: RepositoryRange[];
}
export interface CodeReviewNotes {
  change_summary: string;
  decisions: string[];
  focus_areas: string[];
  traps: string[];
  validation_summary: string;
}
export interface CodeReviewHandoff {
  scope: CodeReviewScope;
  handoff: CodeReviewNotes;
}
export interface ReviewPolicy {
  id: string;
  project_id: string;
  work_item_id: string;
  completion_checkpoint_id: string;
  completion_event_id: string;
  settings_revision: string;
  required_min_priority: number;
  optional_min_priority: number;
  allow_remediation_code_reviews: boolean;
  priority_at_closeout: number;
  remediation_depth: number;
  decision: CodeReviewDecision;
  created_at: string;
}
export interface CodeReview {
  id: string;
  project_id: string;
  work_item_id: string;
  completion_checkpoint_id: string;
  completion_event_id: string;
  policy_decision_id: string;
  answer_id: string | null;
  request_reason: "mandatory" | "recommended";
  schema_version: 1;
  version: number;
  state: "requested" | "completed" | "superseded";
  requesting_client: string;
  requesting_session_id: string;
  requesting_model: string | null;
  scope_sha256: string;
  created_event_id: string;
  created_sequence: string;
  result_id: string | null;
  superseded_by_event_id: string | null;
  created_at: string;
}
export interface WorkFollowUp {
  id: string;
  project_id: string;
  work_item_id: string;
  trigger_event_id: string;
  completion_checkpoint_id: string;
  kind: "code_review_recommendation";
  schema_version: 1;
  version: number;
  audience: "origin_agent" | "origin_human";
  question: string;
  allowed_answers: ("yes" | "no")[];
  required_answer_fields: string[];
  origin_client: string;
  origin_session_id: string;
  origin_model: string | null;
  kind_data: { policy_decision_id: string };
  state: "pending" | "answered" | "superseded";
  answer_id: string | null;
  superseded_by_event_id: string | null;
  created_event_id: string;
  created_sequence: string;
  created_at: string;
}
export interface WorkFollowUpAnswer {
  id: string;
  project_id: string;
  work_item_id: string;
  follow_up_id: string;
  recommend_review: boolean;
  rationale: string;
  actor_client: string;
  actor_session_id: string;
  actor_model: string | null;
  code_review_id: string | null;
  created_event_id: string;
  created_at: string;
}
export interface CodeReviewFinding {
  finding_key: string;
  severity: "critical" | "high" | "medium" | "low";
  title: string;
  repository_key: string;
  path: string;
  location_side: "base" | "head";
  start_line: number | null;
  end_line: number | null;
  problem: string;
  triggering_conditions: string;
  impact: string;
  evidence: string;
  recommended_verification: string;
}
export interface CodeReviewResult {
  mode: "cold" | "warm";
  summary: string;
  coverage: {
    repository_key: string;
    base_commit: string;
    head_commit: string;
  }[];
  limitations: string[];
  findings: CodeReviewFinding[];
  id: string;
  project_id: string;
  work_item_id: string;
  review_id: string;
  scope_sha256: string;
  actor_client: string;
  actor_session_id: string;
  actor_model: string | null;
  lease_generation_id: string;
  claim_event_id: string;
  created_event_id: string;
  created_at: string;
}
export interface ReviewRemediation {
  id: string;
  project_id: string;
  review_id: string;
  result_id: string;
  source_work_item_id: string;
  completion_checkpoint_id: string;
  remediation_work_item_id: string;
  relationship_id: string;
  parent_remediation_id: string | null;
  root_work_item_id: string;
  depth: 1 | 2;
  created_at: string;
}
export interface CodeReviewContext {
  remediation_depth: number;
  current_review: CodeReview | null;
  pending_follow_up: WorkFollowUp | null;
  remediation_origin: ReviewRemediation | null;
}
export interface ReviewSourceState {
  work_item_id: string;
  title: string;
  status: WorkStatus;
  deleted: boolean;
}
export interface CodeReviewDetail {
  review: CodeReview;
  policy_decision: ReviewPolicy;
  scope: CodeReviewScope;
  handoff: CodeReviewNotes;
  result: CodeReviewResult | null;
  remediation: ReviewRemediation | null;
  source_work_state: ReviewSourceState;
}
export interface WorkFollowUpDetail {
  follow_up: WorkFollowUp;
  answer: WorkFollowUpAnswer | null;
  code_review: CodeReview | null;
  source_work_state: ReviewSourceState;
}
export interface ReviewQueueRow {
  id: string;
  project_id: string;
  work_item_id: string;
  title: string;
  work_status: WorkStatus;
  state: string;
  version: number;
  created_sequence: string;
  request_reason: string | null;
  kind: string | null;
  remediation_depth: number;
  review_available: boolean;
  result_id: string | null;
  remediation_work_item_id: string | null;
  lease: LeasePublic | null;
  created_at: string;
}
export interface ReviewQueuePage {
  project_id: string;
  items: ReviewQueueRow[];
  has_more: boolean;
  next_cursor: string;
}
export interface FollowUpAnswerInput {
  kind: "code_review_recommendation";
  recommend_review: boolean;
  rationale: string;
  code_review_handoff?: CodeReviewHandoff;
}
export interface FollowUpAnswerResult {
  follow_up: WorkFollowUp;
  answer: WorkFollowUpAnswer;
  code_review_request?: CodeReview;
  code_review_handoff?: CodeReviewHandoff;
}

const encoder = new TextEncoder();
const forbidden =
  /[\u0000-\u0008\u000b\u000c\u000e-\u001f\u007f-\u009f\u061c\u200e\u200f\u202a-\u202e\u2066-\u206f]/u;
const singleLine = /[\r\n\t\u2028\u2029]/u;
const hash = (value: unknown): value is string =>
  typeof value === "string" && /^[a-f0-9]{64}$/.test(value);
const sequence = (value: unknown): value is string =>
  typeof value === "string" && /^[1-9][0-9]{0,18}$/.test(value);
const version = (value: unknown) => finiteInteger(value, 1, 2147483647);
const nullableId = (value: unknown) => value === null || validUuid(value);
const nullableSequence = (value: unknown) => value === null || sequence(value);
const status = (value: unknown) =>
  ["pending", "deferred", "done", "wont-do", "promoted"].includes(
    String(value),
  );
const fail = (): never => {
  throw new Error("Mnemonic returned invalid code review data.");
};
export function reviewText(
  value: unknown,
  scalars = 2000,
  bytes = 8000,
  multiline = true,
): value is string {
  return (
    boundedText(value, scalars) &&
    !forbidden.test(value) &&
    (multiline || !singleLine.test(value)) &&
    encoder.encode(value).byteLength <= bytes
  );
}
export function reviewJsonBytes(value: unknown): number {
  return encoder.encode(JSON.stringify(value)).byteLength;
}
function model(
  value: unknown,
  fields: readonly string[],
  optional: readonly string[] = [],
): JsonObject {
  const object = objectValue(value);
  if (
    !object ||
    !exactKeys(object, [
      ...fields,
      ...optional.filter((key) => Object.hasOwn(object, key)),
    ])
  )
    fail();
  return object!;
}
function identity(object: JsonObject, projectId?: string, workId?: string) {
  if (
    !["id", "project_id", "work_item_id"].every((key) =>
      validUuid(object[key]),
    ) ||
    (projectId !== undefined && !sameUuid(object.project_id, projectId)) ||
    (workId !== undefined && !sameUuid(object.work_item_id, workId)) ||
    !validUtcDateTime(object.created_at)
  )
    fail();
}
function actor(object: JsonObject, prefix: "actor" | "origin" | "requesting") {
  if (
    !reviewText(object[`${prefix}_client`], 80, 320, false) ||
    !reviewText(object[`${prefix}_session_id`], 200, 800, false) ||
    !(
      object[`${prefix}_model`] === null ||
      reviewText(object[`${prefix}_model`], 120, 480, false)
    )
  )
    fail();
}
function stringList(
  value: unknown,
  maximum: number,
  scalars: number,
): value is string[] {
  return (
    Array.isArray(value) &&
    value.length <= maximum &&
    value.every((item) => reviewText(item, scalars, scalars * 4))
  );
}
export function validRepositoryRange(value: unknown): value is RepositoryRange {
  try {
    const row = model(
      value,
      ["repository_key", "object_format", "base_commit", "head_commit"],
      ["repository_url", "checkout_path"],
    );
    if (
      typeof row.repository_key !== "string" ||
      !/^[A-Za-z0-9][A-Za-z0-9._-]{0,79}$/.test(row.repository_key) ||
      !["sha1", "sha256"].includes(String(row.object_format))
    )
      return false;
    const oid = new RegExp(
      `^[a-f0-9]{${row.object_format === "sha1" ? 40 : 64}}$`,
    );
    if (
      typeof row.base_commit !== "string" ||
      !oid.test(row.base_commit) ||
      typeof row.head_commit !== "string" ||
      !oid.test(row.head_commit) ||
      !("repository_url" in row || "checkout_path" in row)
    )
      return false;
    for (const key of ["repository_url", "checkout_path"] as const) {
      if (!(key in row)) continue;
      if (
        !reviewText(
          row[key],
          key === "repository_url" ? 2000 : 4096,
          16384,
          false,
        ) ||
        /[`$;|<>\\]/.test(row[key] as string)
      )
        return false;
    }
    if (
      row.checkout_path !== undefined &&
      !(row.checkout_path as string).startsWith("/")
    )
      return false;
    if (row.repository_url !== undefined) {
      const url = new URL(row.repository_url as string);
      if (
        url.protocol !== "https:" ||
        !url.hostname ||
        url.username ||
        url.password ||
        url.search ||
        url.hash ||
        /\s/.test(row.repository_url as string)
      )
        return false;
    }
    return true;
  } catch {
    return false;
  }
}
export function decodeReviewScope(value: unknown): CodeReviewScope {
  const scope = model(value, ["repositories"]);
  if (
    !Array.isArray(scope.repositories) ||
    scope.repositories.length < 1 ||
    scope.repositories.length > 10 ||
    !scope.repositories.every(validRepositoryRange) ||
    reviewJsonBytes(scope) > 65536
  )
    fail();
  const rows = scope.repositories as RepositoryRange[];
  if (
    new Set(rows.map((row) => row.repository_key)).size !== rows.length ||
    new Set(
      rows.map((row) =>
        JSON.stringify([
          row.repository_url ?? null,
          row.checkout_path ?? null,
          row.base_commit,
          row.head_commit,
        ]),
      ),
    ).size !== rows.length
  )
    fail();
  return scope as unknown as CodeReviewScope;
}
export function decodeReviewNotes(value: unknown): CodeReviewNotes {
  const notes = model(value, [
    "change_summary",
    "decisions",
    "focus_areas",
    "traps",
    "validation_summary",
  ]);
  if (
    !reviewText(notes.change_summary, 4000, 16000) ||
    !reviewText(notes.validation_summary, 4000, 16000) ||
    !["decisions", "focus_areas", "traps"].every((key) =>
      stringList(notes[key], 20, 2000),
    ) ||
    reviewJsonBytes(notes) > 65536
  )
    fail();
  return notes as unknown as CodeReviewNotes;
}
export function validReviewHandoff(value: unknown): value is CodeReviewHandoff {
  try {
    const input = model(value, ["scope", "handoff"]);
    decodeReviewScope(input.scope);
    decodeReviewNotes(input.handoff);
    return true;
  } catch {
    return false;
  }
}
export function validFollowUpAnswer(
  value: unknown,
): value is FollowUpAnswerInput {
  try {
    const answer = model(
      value,
      ["kind", "recommend_review", "rationale"],
      ["code_review_handoff"],
    );
    return (
      answer.kind === "code_review_recommendation" &&
      typeof answer.recommend_review === "boolean" &&
      reviewText(answer.rationale) &&
      (answer.recommend_review
        ? validReviewHandoff(answer.code_review_handoff)
        : !Object.hasOwn(answer, "code_review_handoff"))
    );
  } catch {
    return false;
  }
}
export function decodeReviewPolicy(
  value: unknown,
  projectId?: string,
  workId?: string,
): ReviewPolicy {
  const row = model(value, [
    "id",
    "project_id",
    "work_item_id",
    "completion_checkpoint_id",
    "completion_event_id",
    "settings_revision",
    "required_min_priority",
    "optional_min_priority",
    "allow_remediation_code_reviews",
    "priority_at_closeout",
    "remediation_depth",
    "decision",
    "created_at",
  ]);
  identity(row, projectId, workId);
  if (
    !validUuid(row.completion_checkpoint_id) ||
    !sequence(row.completion_event_id) ||
    !sequence(row.settings_revision) ||
    !validReviewThreshold(row.required_min_priority) ||
    !validReviewThreshold(row.optional_min_priority) ||
    typeof row.allow_remediation_code_reviews !== "boolean" ||
    !finiteInteger(row.priority_at_closeout, 0, 100) ||
    !finiteInteger(row.remediation_depth, 0, 2)
  )
    fail();
  if (
    row.decision !==
    codeReviewDecision(
      {
        code_review_required_min_priority: row.required_min_priority as number,
        code_review_optional_min_priority: row.optional_min_priority as number,
        allow_remediation_code_reviews:
          row.allow_remediation_code_reviews as boolean,
      },
      row.priority_at_closeout as number,
      row.remediation_depth as number,
    )
  )
    fail();
  return row as unknown as ReviewPolicy;
}
export function decodeCodeReview(
  value: unknown,
  projectId?: string,
  workId?: string,
): CodeReview {
  const row = model(value, [
    "id",
    "project_id",
    "work_item_id",
    "completion_checkpoint_id",
    "completion_event_id",
    "policy_decision_id",
    "answer_id",
    "request_reason",
    "schema_version",
    "version",
    "state",
    "requesting_client",
    "requesting_session_id",
    "requesting_model",
    "scope_sha256",
    "created_event_id",
    "created_sequence",
    "result_id",
    "superseded_by_event_id",
    "created_at",
  ]);
  identity(row, projectId, workId);
  actor(row, "requesting");
  if (
    !validUuid(row.completion_checkpoint_id) ||
    !validUuid(row.policy_decision_id) ||
    !nullableId(row.answer_id) ||
    !sequence(row.completion_event_id) ||
    !sequence(row.created_event_id) ||
    !sequence(row.created_sequence) ||
    !version(row.version) ||
    row.schema_version !== 1 ||
    !hash(row.scope_sha256) ||
    !["requested", "completed", "superseded"].includes(String(row.state)) ||
    !["mandatory", "recommended"].includes(String(row.request_reason)) ||
    (row.request_reason === "recommended") !== (row.answer_id !== null) ||
    !nullableId(row.result_id) ||
    !nullableSequence(row.superseded_by_event_id) ||
    (row.state === "completed") !== (row.result_id !== null) ||
    (row.state === "superseded") !== (row.superseded_by_event_id !== null) ||
    (row.state === "requested" ? row.version !== 1 : row.version !== 2)
  )
    fail();
  return row as unknown as CodeReview;
}
export function decodeWorkFollowUp(
  value: unknown,
  projectId?: string,
  workId?: string,
): WorkFollowUp {
  const row = model(value, [
    "id",
    "project_id",
    "work_item_id",
    "trigger_event_id",
    "completion_checkpoint_id",
    "kind",
    "schema_version",
    "version",
    "audience",
    "question",
    "allowed_answers",
    "required_answer_fields",
    "origin_client",
    "origin_session_id",
    "origin_model",
    "kind_data",
    "state",
    "answer_id",
    "superseded_by_event_id",
    "created_event_id",
    "created_sequence",
    "created_at",
  ]);
  identity(row, projectId, workId);
  actor(row, "origin");
  const kind = model(row.kind_data, ["policy_decision_id"]);
  if (
    !validUuid(kind.policy_decision_id) ||
    !validUuid(row.completion_checkpoint_id) ||
    !sequence(row.trigger_event_id) ||
    !sequence(row.created_event_id) ||
    !sequence(row.created_sequence) ||
    !version(row.version) ||
    row.schema_version !== 1 ||
    row.kind !== "code_review_recommendation" ||
    !["origin_agent", "origin_human"].includes(String(row.audience)) ||
    !reviewText(row.question, 4000, 8192) ||
    JSON.stringify(row.allowed_answers) !== '["yes","no"]' ||
    !stringList(row.required_answer_fields, 10, 80) ||
    !["pending", "answered", "superseded"].includes(String(row.state)) ||
    !nullableId(row.answer_id) ||
    !nullableSequence(row.superseded_by_event_id) ||
    (row.state === "answered") !== (row.answer_id !== null) ||
    (row.state === "superseded") !== (row.superseded_by_event_id !== null) ||
    (row.state === "pending" ? row.version !== 1 : row.version !== 2)
  )
    fail();
  return row as unknown as WorkFollowUp;
}
function decodeAnswer(
  value: unknown,
  projectId: string,
  workId: string,
): WorkFollowUpAnswer {
  const row = model(value, [
    "id",
    "project_id",
    "work_item_id",
    "follow_up_id",
    "recommend_review",
    "rationale",
    "actor_client",
    "actor_session_id",
    "actor_model",
    "code_review_id",
    "created_event_id",
    "created_at",
  ]);
  identity(row, projectId, workId);
  actor(row, "actor");
  if (
    !validUuid(row.follow_up_id) ||
    typeof row.recommend_review !== "boolean" ||
    !reviewText(row.rationale) ||
    !nullableId(row.code_review_id) ||
    row.recommend_review !== (row.code_review_id !== null) ||
    !sequence(row.created_event_id)
  )
    fail();
  return row as unknown as WorkFollowUpAnswer;
}
export function decodeRemediation(
  value: unknown,
  projectId: string,
): ReviewRemediation {
  const row = model(value, [
    "id",
    "project_id",
    "review_id",
    "result_id",
    "source_work_item_id",
    "completion_checkpoint_id",
    "remediation_work_item_id",
    "relationship_id",
    "parent_remediation_id",
    "root_work_item_id",
    "depth",
    "created_at",
  ]);
  if (
    !sameUuid(row.project_id, projectId) ||
    ![
      "id",
      "review_id",
      "result_id",
      "source_work_item_id",
      "completion_checkpoint_id",
      "remediation_work_item_id",
      "relationship_id",
      "root_work_item_id",
    ].every((key) => validUuid(row[key])) ||
    !nullableId(row.parent_remediation_id) ||
    !finiteInteger(row.depth, 1, 2) ||
    !validUtcDateTime(row.created_at) ||
    sameUuid(row.source_work_item_id, row.remediation_work_item_id) ||
    (row.depth === 1) !== (row.parent_remediation_id === null) ||
    (row.depth === 1 &&
      !sameUuid(row.root_work_item_id, row.source_work_item_id))
  )
    fail();
  return row as unknown as ReviewRemediation;
}
export function decodeCodeReviewContext(
  value: unknown,
  projectId: string,
  workId: string,
): CodeReviewContext {
  const row = model(value, [
    "remediation_depth",
    "current_review",
    "pending_follow_up",
    "remediation_origin",
  ]);
  if (!finiteInteger(row.remediation_depth, 0, 2)) fail();
  const review =
    row.current_review === null
      ? null
      : decodeCodeReview(row.current_review, projectId, workId);
  const question =
    row.pending_follow_up === null
      ? null
      : decodeWorkFollowUp(row.pending_follow_up, projectId, workId);
  const origin =
    row.remediation_origin === null
      ? null
      : decodeRemediation(row.remediation_origin, projectId);
  if (
    (review && review.state !== "requested") ||
    (question && question.state !== "pending") ||
    (review && question) ||
    (row.remediation_depth === 0) !== (origin === null) ||
    (origin &&
      (origin.depth !== row.remediation_depth ||
        !sameUuid(origin.remediation_work_item_id, workId))) ||
    (row.remediation_depth === 2 && (review || question))
  )
    fail();
  return {
    remediation_depth: row.remediation_depth as number,
    current_review: review,
    pending_follow_up: question,
    remediation_origin: origin,
  };
}
function decodeSource(value: unknown, workId: string): ReviewSourceState {
  const row = model(value, ["work_item_id", "title", "status", "deleted"]);
  if (
    !sameUuid(row.work_item_id, workId) ||
    !reviewText(row.title, 200, 800, false) ||
    !status(row.status) ||
    typeof row.deleted !== "boolean"
  )
    fail();
  return row as unknown as ReviewSourceState;
}
export function decodeWorkFollowUpDetail(
  value: unknown,
  projectId: string,
  workId: string,
  id: string,
): WorkFollowUpDetail {
  const row = model(value, [
    "follow_up",
    "answer",
    "code_review",
    "source_work_state",
  ]);
  const follow = decodeWorkFollowUp(row.follow_up, projectId, workId);
  const answer =
    row.answer === null ? null : decodeAnswer(row.answer, projectId, workId);
  const review =
    row.code_review === null
      ? null
      : decodeCodeReview(row.code_review, projectId, workId);
  if (
    !sameUuid(follow.id, id) ||
    (follow.state === "answered") !== (answer !== null) ||
    (answer &&
      (!sameUuid(answer.follow_up_id, id) ||
        !sameUuid(answer.id, follow.answer_id) ||
        answer.actor_client !== follow.origin_client ||
        answer.actor_session_id !== follow.origin_session_id)) ||
    Boolean(answer?.recommend_review) !== (review !== null) ||
    (review &&
      (!sameUuid(review.id, answer?.code_review_id) ||
        !sameUuid(review.answer_id, answer?.id)))
  )
    fail();
  return {
    follow_up: follow,
    answer,
    code_review: review,
    source_work_state: decodeSource(row.source_work_state, workId),
  };
}
export function decodeFollowUpAnswerResult(
  value: unknown,
  projectId: string,
  workId: string,
  id: string,
  expectedVersion: number,
  input: FollowUpAnswerInput,
  author: MutationActor,
): FollowUpAnswerResult {
  const row = model(
    value,
    ["follow_up", "answer"],
    ["code_review_request", "code_review_handoff"],
  );
  const follow = decodeWorkFollowUp(row.follow_up, projectId, workId);
  const answer = decodeAnswer(row.answer, projectId, workId);
  const review =
    row.code_review_request === undefined
      ? undefined
      : decodeCodeReview(row.code_review_request, projectId, workId);
  if (
    !sameUuid(follow.id, id) ||
    follow.state !== "answered" ||
    follow.version !== expectedVersion + 1 ||
    !sameUuid(answer.follow_up_id, id) ||
    !sameUuid(answer.id, follow.answer_id) ||
    answer.recommend_review !== input.recommend_review ||
    answer.rationale !== input.rationale ||
    answer.actor_client !== author.actor_client ||
    answer.actor_session_id !== author.actor_session_id ||
    answer.actor_model !== (author.actor_model ?? null) ||
    Boolean(review) !== input.recommend_review ||
    follow.origin_client !== author.actor_client ||
    follow.origin_session_id !== author.actor_session_id ||
    (review &&
      (review.state !== "requested" ||
        !sameUuid(review.id, answer.code_review_id) ||
        !sameUuid(review.answer_id, answer.id) ||
        !sameUuid(
          review.policy_decision_id,
          follow.kind_data.policy_decision_id,
        ) ||
        !sameUuid(
          review.completion_checkpoint_id,
          follow.completion_checkpoint_id,
        ) ||
        review.completion_event_id !== follow.trigger_event_id ||
        review.requesting_client !== author.actor_client ||
        review.requesting_session_id !== author.actor_session_id)) ||
    input.recommend_review !== Object.hasOwn(row, "code_review_handoff") ||
    (input.recommend_review &&
      (!validReviewHandoff(row.code_review_handoff) ||
        !jsonEqual(row.code_review_handoff, input.code_review_handoff)))
  )
    fail();
  return {
    follow_up: follow,
    answer,
    ...(review
      ? {
          code_review_request: review,
          code_review_handoff: row.code_review_handoff as CodeReviewHandoff,
        }
      : {}),
  };
}

function decodeFinding(value: unknown): CodeReviewFinding {
  const row = model(value, [
    "finding_key",
    "severity",
    "title",
    "repository_key",
    "path",
    "location_side",
    "start_line",
    "end_line",
    "problem",
    "triggering_conditions",
    "impact",
    "evidence",
    "recommended_verification",
  ]);
  if (
    typeof row.finding_key !== "string" ||
    !/^F[0-9]{3}$/.test(row.finding_key) ||
    !["critical", "high", "medium", "low"].includes(String(row.severity)) ||
    !reviewText(row.title, 200, 800, false) ||
    !reviewText(row.repository_key, 80, 80, false) ||
    !reviewText(row.path, 4096, 16384, false) ||
    /^(?:\/|\\)/.test(row.path as string) ||
    (row.path as string).includes("\\") ||
    (row.path as string).split("/").includes("..") ||
    !["base", "head"].includes(String(row.location_side)) ||
    !(
      row.start_line === null || finiteInteger(row.start_line, 1, 2147483647)
    ) ||
    !(
      row.end_line === null ||
      (finiteInteger(row.end_line, 1, 2147483647) &&
        row.start_line !== null &&
        Number(row.end_line) >= Number(row.start_line))
    ) ||
    ![
      "problem",
      "triggering_conditions",
      "impact",
      "evidence",
      "recommended_verification",
    ].every((key) => reviewText(row[key])) ||
    reviewJsonBytes(row) > 8192
  )
    fail();
  return row as unknown as CodeReviewFinding;
}
function decodeResult(
  value: unknown,
  review: CodeReview,
  scope: CodeReviewScope,
): CodeReviewResult {
  const row = model(value, [
    "mode",
    "summary",
    "coverage",
    "limitations",
    "findings",
    "id",
    "project_id",
    "work_item_id",
    "review_id",
    "scope_sha256",
    "actor_client",
    "actor_session_id",
    "actor_model",
    "lease_generation_id",
    "claim_event_id",
    "created_event_id",
    "created_at",
  ]);
  identity(row, review.project_id, review.work_item_id);
  actor(row, "actor");
  if (
    !sameUuid(row.review_id, review.id) ||
    !sameUuid(row.id, review.result_id) ||
    row.scope_sha256 !== review.scope_sha256 ||
    !["cold", "warm"].includes(String(row.mode)) ||
    !reviewText(row.summary, 4000, 16000) ||
    !stringList(row.limitations, 20, 1000) ||
    !validUuid(row.lease_generation_id) ||
    !sequence(row.claim_event_id) ||
    !sequence(row.created_event_id) ||
    !Array.isArray(row.coverage) ||
    row.coverage.length !== scope.repositories.length ||
    !Array.isArray(row.findings) ||
    row.findings.length > 100
  )
    fail();
  const coverage = (row.coverage as unknown[]).map((entry) => {
    const cover = model(entry, [
      "repository_key",
      "base_commit",
      "head_commit",
    ]);
    const repository = scope.repositories.find(
      (repo) => repo.repository_key === cover.repository_key,
    );
    if (
      !repository ||
      cover.base_commit !== repository.base_commit ||
      cover.head_commit !== repository.head_commit
    )
      fail();
    return cover as unknown as CodeReviewResult["coverage"][number];
  });
  const findings = (row.findings as unknown[]).map(decodeFinding);
  if (
    new Set(coverage.map((item) => item.repository_key)).size !==
      coverage.length ||
    new Set(findings.map((item) => item.finding_key)).size !==
      findings.length ||
    findings.some(
      (item) =>
        !coverage.some((cover) => cover.repository_key === item.repository_key),
    ) ||
    reviewJsonBytes({
      mode: row.mode,
      summary: row.summary,
      coverage,
      limitations: row.limitations,
      findings,
    }) > 65536
  )
    fail();
  return { ...row, coverage, findings } as unknown as CodeReviewResult;
}
export function decodeCodeReviewDetail(
  value: unknown,
  projectId: string,
  workId: string,
  id: string,
): CodeReviewDetail {
  const row = model(value, [
    "review",
    "policy_decision",
    "scope",
    "handoff",
    "result",
    "remediation",
    "source_work_state",
  ]);
  const review = decodeCodeReview(row.review, projectId, workId);
  const policy = decodeReviewPolicy(row.policy_decision, projectId, workId);
  const scope = decodeReviewScope(row.scope);
  const result =
    row.result === null ? null : decodeResult(row.result, review, scope);
  const remediation =
    row.remediation === null
      ? null
      : decodeRemediation(row.remediation, projectId);
  if (
    !sameUuid(review.id, id) ||
    !sameUuid(review.policy_decision_id, policy.id) ||
    !sameUuid(
      review.completion_checkpoint_id,
      policy.completion_checkpoint_id,
    ) ||
    review.completion_event_id !== policy.completion_event_id ||
    policy.decision !==
      (review.request_reason === "mandatory"
        ? "mandatory"
        : "ask_recommendation") ||
    (review.state === "completed") !== Boolean(result) ||
    Boolean(result?.findings.length) !== Boolean(remediation) ||
    (remediation &&
      (!sameUuid(remediation.review_id, id) ||
        !sameUuid(remediation.result_id, result?.id) ||
        !sameUuid(remediation.source_work_item_id, workId) ||
        remediation.depth !== policy.remediation_depth + 1))
  )
    fail();
  return {
    review,
    policy_decision: policy,
    scope,
    handoff: decodeReviewNotes(row.handoff),
    result,
    remediation,
    source_work_state: decodeSource(row.source_work_state, workId),
  };
}
export function decodeReviewQueuePage(
  value: unknown,
  projectId: string,
  kind: "reviews" | "follow-ups",
  limit = 20,
): ReviewQueuePage {
  const page = model(value, ["project_id", "items", "has_more", "next_cursor"]);
  if (
    !sameUuid(page.project_id, projectId) ||
    !Array.isArray(page.items) ||
    page.items.length > limit ||
    typeof page.has_more !== "boolean" ||
    typeof page.next_cursor !== "string" ||
    !validUnicode(page.next_cursor) ||
    page.next_cursor.length > 4096
  )
    fail();
  const items = (page.items as unknown[]).map((value) => {
    const row = model(value, [
      "id",
      "project_id",
      "work_item_id",
      "title",
      "work_status",
      "state",
      "version",
      "created_sequence",
      "request_reason",
      "kind",
      "remediation_depth",
      "review_available",
      "result_id",
      "remediation_work_item_id",
      "lease",
      "created_at",
    ]);
    identity(row, projectId);
    if (
      !reviewText(row.title, 200, 800, false) ||
      !status(row.work_status) ||
      !version(row.version) ||
      !sequence(row.created_sequence) ||
      !finiteInteger(row.remediation_depth, 0, 2) ||
      typeof row.review_available !== "boolean" ||
      !nullableId(row.result_id) ||
      !nullableId(row.remediation_work_item_id) ||
      !(kind === "reviews"
        ? ["requested", "completed", "superseded"].includes(
            String(row.state),
          ) &&
          ["mandatory", "recommended"].includes(String(row.request_reason)) &&
          row.kind === null
        : ["pending", "answered", "superseded"].includes(String(row.state)) &&
          row.kind === "code_review_recommendation" &&
          row.request_reason === null) ||
      encoder.encode(JSON.stringify(row)).byteLength > 8192
    )
      fail();
    if (row.lease !== null) {
      const lease = model(
        row.lease,
        [
          "holder_client",
          "holder_session_id",
          "acquired_at",
          "renewed_at",
          "expires_at",
        ],
        ["purpose", "code_review_id", "mode"],
      );
      if (
        !reviewText(lease.holder_client, 80, 320, false) ||
        !reviewText(lease.holder_session_id, 200, 800, false) ||
        !["acquired_at", "renewed_at", "expires_at"].every((key) =>
          validUtcDateTime(lease[key]),
        ) ||
        kind !== "reviews" ||
        lease.purpose !== "code_review" ||
        !sameUuid(lease.code_review_id, row.id) ||
        !["cold", "warm"].includes(String(lease.mode))
      )
        fail();
    }
    return row as unknown as ReviewQueueRow;
  });
  if (
    new Set(items.map((item) => item.id)).size !== items.length ||
    (page.has_more && (!items.length || !page.next_cursor)) ||
    items.some(
      (item, index) =>
        index > 0 &&
        BigInt(item.created_sequence) >=
          BigInt(items[index - 1].created_sequence),
    )
  )
    fail();
  return {
    project_id: projectId,
    items,
    has_more: page.has_more as boolean,
    next_cursor: page.next_cursor as string,
  };
}
