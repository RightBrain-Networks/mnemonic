import * as f from "./phase12-fixtures.mjs";
export const reviewId = "aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa";
export const followId = "bbbbbbbb-bbbb-4bbb-8bbb-bbbbbbbbbbbb";
export const policyId = "cccccccc-cccc-4ccc-8ccc-cccccccccccc";
export const answerId = "dddddddd-dddd-4ddd-8ddd-dddddddddddd";
export const resultId = "eeeeeeee-eeee-4eee-8eee-eeeeeeeeeeee";
export const handoff = {
  scope: {
    repositories: [
      {
        repository_key: "app",
        repository_url: "https://example.com/owner/repo",
        checkout_path: "/srv/repo",
        object_format: "sha1",
        base_commit: "a".repeat(40),
        head_commit: "b".repeat(40),
      },
    ],
  },
  handoff: {
    change_summary: "HANDOFF_CANARY",
    decisions: ["DECISION_CANARY"],
    focus_areas: [],
    traps: [],
    validation_summary: "Tests passed.",
  },
};
export const policy = {
  id: policyId,
  project_id: f.project,
  work_item_id: f.work,
  completion_checkpoint_id: f.checkpointId,
  completion_event_id: "8",
  settings_revision: "3",
  required_min_priority: 0,
  optional_min_priority: 0,
  allow_remediation_code_reviews: false,
  priority_at_closeout: 5,
  remediation_depth: 0,
  decision: "mandatory",
  created_at: f.timestamp,
};
export const review = {
  id: reviewId,
  project_id: f.project,
  work_item_id: f.work,
  completion_checkpoint_id: f.checkpointId,
  completion_event_id: "8",
  policy_decision_id: policyId,
  answer_id: null,
  request_reason: "mandatory",
  schema_version: 1,
  version: 1,
  state: "requested",
  requesting_client: "dashboard",
  requesting_session_id: "tab-1",
  requesting_model: null,
  scope_sha256: "a".repeat(64),
  created_event_id: "10",
  created_sequence: "11",
  result_id: null,
  superseded_by_event_id: null,
  created_at: f.timestamp,
};
export const question = {
  id: followId,
  project_id: f.project,
  work_item_id: f.work,
  trigger_event_id: "8",
  completion_checkpoint_id: f.checkpointId,
  kind: "code_review_recommendation",
  schema_version: 1,
  version: 1,
  audience: "origin_human",
  question: "Do you recommend a code review?",
  allowed_answers: ["yes", "no"],
  required_answer_fields: ["recommend_review", "rationale"],
  origin_client: "dashboard",
  origin_session_id: "tab-1",
  origin_model: null,
  kind_data: { policy_decision_id: policyId },
  state: "pending",
  answer_id: null,
  superseded_by_event_id: null,
  created_event_id: "10",
  created_sequence: "11",
  created_at: f.timestamp,
};
export const answer = {
  id: answerId,
  project_id: f.project,
  work_item_id: f.work,
  follow_up_id: followId,
  recommend_review: false,
  rationale: "A comprehensive review already finished.",
  ...f.actor,
  code_review_id: null,
  created_event_id: "12",
  created_at: f.timestamp,
};
export const source = {
  work_item_id: f.work,
  title: f.workItem.title,
  status: "done",
  deleted: false,
};
export const reviewDetail = {
  review,
  policy_decision: policy,
  ...handoff,
  result: null,
  remediation: null,
  source_work_state: source,
};
export const negativeDetail = {
  follow_up: {
    ...question,
    state: "answered",
    version: 2,
    answer_id: answerId,
  },
  answer,
  code_review: null,
  source_work_state: source,
};
export const negativeResponse = { follow_up: negativeDetail.follow_up, answer };
export const recommendedReview = {
  ...review,
  answer_id: answerId,
  request_reason: "recommended",
};
export const affirmativeResponse = {
  follow_up: negativeDetail.follow_up,
  answer: { ...answer, recommend_review: true, code_review_id: reviewId },
  code_review_request: recommendedReview,
  code_review_handoff: handoff,
};
export const queueRow = {
  id: reviewId,
  project_id: f.project,
  work_item_id: f.work,
  title: f.workItem.title,
  work_status: "done",
  state: "requested",
  version: 1,
  created_sequence: "11",
  request_reason: "mandatory",
  kind: null,
  remediation_depth: 0,
  review_available: true,
  result_id: null,
  remediation_work_item_id: null,
  lease: null,
  created_at: f.timestamp,
};
export const queuePage = {
  project_id: f.project,
  items: [queueRow],
  has_more: false,
  next_cursor: "",
};
