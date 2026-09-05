export const project = "11111111-1111-4111-8111-111111111111";
export const work = "22222222-2222-4222-8222-222222222222";
export const reportId = "33333333-3333-4333-8333-333333333333";
export const checkpointId = "44444444-4444-4444-8444-444444444444";
export const stream = "55555555-5555-4555-8555-555555555555";
export const operation = "66666666-6666-4666-8666-666666666666";
export const actionId = "77777777-7777-4777-8777-777777777777";
export const followWork = "88888888-8888-4888-8888-888888888888";
export const timestamp = "2026-09-05T20:00:00Z";
export const reportInput = { summary: "The dashboard font is consistent across its main pages. The change is ready to review and has not been deployed.", fyi_items: ["I chose Arial because it is widely available; create a follow-up if you prefer another font."], prompt_revision: "3" };
export const actor = { actor_client: "dashboard", actor_session_id: "tab-1", actor_model: null };
export const report = { ...reportInput, id: reportId, project_id: project, work_item_id: work,
  closeout_event_id: "8", closeout_work_version: 2, closeout_status: "done", completion_checkpoint_id: checkpointId,
  work_title_at_closeout: "Consistent dashboard font", ...actor, prompt_sha256: "a".repeat(64), created_at: timestamp };
export const envelope = { report, created_sequence: "9", human_dismissed: false, human_dismissal: null,
  source_work_state: { work_item_id: work, status: "done", canonical_work_item_id: work, deleted: false }, follow_up_count: "0" };
export const workItem = { id: work, project_id: project, title: report.work_title_at_closeout, summary: "Use a readable font.", status: "done", priority: 5, initial_checkpoint_id: checkpointId, version: 2, created_at: timestamp, updated_at: timestamp };
export const checkpointInput = { prompt: "Changed the dashboard font and checked the supported layouts.", source_client: "dashboard", source_session_id: "tab-1", source_model: null };
export const checkpoint = { id: checkpointId, work_item_id: work, kind: "completion", ...checkpointInput,
  source_session_url: null, repository_branch: null, verified_against: null, tags: [],
  source_metadata: {}, migration_origin: null, legacy_record_id: null, created_at: timestamp };
export function cursor(fields) { return Buffer.from(JSON.stringify(Object.fromEntries(Object.entries({v:1, project_id:project, stream_id:stream, ...fields}).sort(([a],[b]) => a.localeCompare(b))))).toString("base64url"); }
export function activity(overrides = {}) { return { sequence: "9", kind: "job_completion_report_created", work_event_id: null, event_type: null, work_item_id: work, job_completion_report_id: reportId, human_dismissal_id: null, follow_up_id: null, settings_revision: null, lease_generation_id: null, recorded_at: timestamp, origin: "live", ...overrides }; }
export function reportPage(overrides = {}) { return { project_id: project, stream_id: stream, dismissal: "undismissed", work_item_id: null, as_of_sequence: "9", items: [structuredClone(envelope)], has_more: false, next_cursor: null, ...overrides }; }
