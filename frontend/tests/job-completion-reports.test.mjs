import assert from "node:assert/strict";
import test from "node:test";
import { jobReportDraftHasEdits, emptyJobReportDraft, decodeJobReport, decodeReportEnvelope, decodeReportPage, decodeReportCount, decodeProjectSettings, validJobReportInput, validReportPrompt } from "../lib/job-completion-reports.ts";
import { decodePhase12Cursor, decimalString } from "../lib/activity-cursors.ts";
import { allowedQueryKeys, invalidMutationBody, phase12ResponseLimitBytes } from "../lib/proxy-policy.ts";
import { classifyMutationResponse } from "../lib/mutation-responses.ts";
import { readBoundedJson } from "../lib/bounded-json.ts";
import * as f from "./phase12-fixtures.mjs";

test("report prose enforces Unicode scalar, control, single-paragraph and aggregate byte boundaries without rewriting", () => {
  assert.ok(validJobReportInput(f.reportInput));
  assert.ok(validJobReportInput({...f.reportInput,summary:"  مرحبًا. 汉字 🙂  ", fyi_items:[]}));
  assert.ok(validJobReportInput({...f.reportInput,summary:"🙂".repeat(2000), fyi_items:[]}));
  for (const summary of ["", " \t", "one\ntwo", "one\u2028two", "x\u206a", "x\ud800", "x\u202e", "x\u009f", "🙂".repeat(2001)]) {
    assert.equal(validJobReportInput({...f.reportInput,summary}), false);
  }
  for (const bad of [{fyi_items:null},{fyi_items:[{}]},{fyi_items:["x\ny"]},{fyi_items:Array(11).fill("FYI.")}, {fyi_items:["x".repeat(601)]}, {prompt_revision:3}, {prompt_revision:"03"}, {summary:null}, {extra:"secret"}]) assert.equal(validJobReportInput({...f.reportInput,...bad}), false);
  assert.equal(validJobReportInput({...f.reportInput, summary:"🙂".repeat(2000), fyi_items:Array(10).fill("🙂".repeat(600))}), false);
  assert.ok(validJobReportInput({...f.reportInput,fyi_items:["Dr. Smith checked v1.2.","Dr. Smith checked v1.2."]}));
  for (const value of ["1", "9223372036854775807"]) assert.ok(decimalString(value,true));
  for (const value of [1,"0","01","-1","9223372036854775808","１"]) assert.equal(decimalString(value,true),false);
});

test("strict report reads bind exact closeout provenance and reject mutable or hidden fields", () => {
  assert.deepEqual(decodeJobReport(f.report,f.project),f.report);
  for (const overrides of [{actor_model:undefined},{closeout_event_id:8},{closeout_status:"pending"},{completion_checkpoint_id:null},{prompt_sha256:"A".repeat(64)},{authoring_prompt:"Hidden"},{human_dismissed:true}]) assert.throws(() => decodeJobReport({...f.report,...overrides},f.project));
  assert.deepEqual(decodeReportEnvelope(f.envelope,f.project),f.envelope);
  assert.throws(() => decodeReportEnvelope({...f.envelope,source_work_state:{...f.envelope.source_work_state,work_item_id:f.followWork}},f.project));
  assert.throws(() => decodeReportEnvelope({...f.envelope,human_dismissed:true},f.project));
  assert.throws(() => decodeReportEnvelope({...f.envelope,follow_up_count:0},f.project));
  assert.equal(decodeReportEnvelope({...f.envelope,source_work_state:{...f.envelope.source_work_state,deleted:true,status:"pending",canonical_work_item_id:f.followWork}},f.project).report.closeout_status,"done");
});

test("settings are exact revisioned aggregates with independent editable prompt bounds", () => {
  const settings={project_id:f.project,revision:"3",recall_pointer_template:null,job_completion_report_prompt:"Write clearly.\nUse one paragraph.\tNo jargon."};
  assert.deepEqual(decodeProjectSettings(settings,f.project),settings);
  assert.ok(validReportPrompt(settings.job_completion_report_prompt));
  for (const prompt of [" ","x\u206f","x\0","x".repeat(8001),"🙂".repeat(4097)]) assert.equal(validReportPrompt(prompt),false);
  const path=`projects/${f.project}/settings`;
  for (const payload of [{expected_revision:"3",recall_pointer_template:null},{expected_revision:"3",job_completion_report_prompt:null},{expected_revision:"3",job_completion_report_prompt:"Write clearly."},{expected_revision:"3",recall_pointer_template:"Recall",job_completion_report_prompt:"Write clearly."}]) assert.equal(invalidMutationBody(path,"PATCH",payload),null);
  for (const payload of [{expected_revision:3,job_completion_report_prompt:"Text"},{job_completion_report_prompt:"Text"},{expected_revision:"3"},{expected_revision:"3",job_completion_report_prompt:" "}]) assert.match(invalidMutationBody(path,"PATCH",payload),/allowlist/);
});

test("report pagination rejects wrong cursors, scope, filters, reordered sequences and stale high water", () => {
  assert.equal(decodeReportPage(f.reportPage(),f.project).items.length,1);
  const next=f.cursor({kind:"reports",dismissal:"undismissed",work_item_id:null,upper:"9",last:"9"});
  assert.equal(decodeReportPage(f.reportPage({has_more:true,next_cursor:next}),f.project).next_cursor,next);
  for (const overrides of [{next_cursor:next},{has_more:true},{work_item_id:f.work},{as_of_sequence:"8"},{items:[f.envelope,f.envelope]},{items:[{...f.envelope,human_dismissed:true}]}]) assert.throws(() => decodeReportPage(f.reportPage(overrides),f.project));
  for (const bad of [next+"=", next+"A", "!", "A", f.cursor({kind:"activity",after:"9"}),f.cursor({kind:"reports",dismissal:"all",work_item_id:null,upper:"9",last:"9"})]) assert.throws(() => decodeReportPage(f.reportPage({has_more:true,next_cursor:bad}),f.project));
  assert.deepEqual(decodeReportCount({project_id:f.project,undismissed_count:"9007199254740993",as_of_sequence:"9"},f.project).undismissed_count,"9007199254740993");
  assert.throws(() => decodePhase12Cursor(Buffer.from('{"v":1,"v":1}').toString('base64url'),f.project,"reports"));
});

test("browser allowlist exposes bounded report reads and exact human mutations without control leakage", () => {
  const base=`projects/${f.project}/job-completion-reports`;
  assert.deepEqual(allowedQueryKeys(`${base}/count`,"GET"),[]);
  assert.deepEqual(allowedQueryKeys(base,"GET"),["dismissal","work_item_id","limit","cursor"]);
  assert.equal(allowedQueryKeys(base,"POST"),null);
  assert.equal(allowedQueryKeys(`${base}/${f.reportId}/undismiss`,"POST"),null);
  const dismissal={client_operation_id:f.operation,actor:f.actor};
  assert.equal(invalidMutationBody(`${base}/${f.reportId}/dismiss`,"POST",dismissal),null);
  assert.match(invalidMutationBody(`${base}/${f.reportId}/dismiss`,"POST",{...dismissal,report_id:f.reportId}),/allowlist/);
  const follow={...dismissal,title:"Comic Sans",summary:"Change font",priority:1,initial_checkpoint:f.checkpointInput};
  const path=`${base}/${f.reportId}/follow-ups`;
  assert.equal(invalidMutationBody(path,"POST",follow),null);
  for (const extra of [{status:"pending"},{source_work_item_id:f.work},{initial_relationships:[]},{actor:{...f.actor,actor_session_id:"other"}},{initial_checkpoint:{...f.checkpointInput,job_completion_report:f.reportInput}}]) assert.match(invalidMutationBody(path,"POST",{...follow,...extra}),/allowlist/);
  assert.equal(phase12ResponseLimitBytes(`${base}/count`),1024);
});

function request(kind,body,extra={}) { return {kind,method:kind==="update_work"?"PATCH":"POST",path:`/projects/${f.project}/work-items/${f.work}${kind==="complete_work"?"/complete":""}`,operationId:f.operation,body:JSON.stringify({...body,client_operation_id:f.operation}),...extra}; }
async function classify(req,value,status=200) { return classifyMutationResponse(req,Response.json(value,{status})); }

test("closeout success validates report-bearing and historical sparse requests independently", async () => {
  const body={expected_version:1,checkpoint:f.checkpointInput,job_completion_report:f.reportInput};
  const response={work_item:f.workItem,checkpoint:f.checkpoint,job_completion_report:f.report};
  const req=request("complete_work",body);
  assert.equal((await classify(req,response)).type,"success");
  const {job_completion_report: _report,...sparse}=response;
  assert.equal((await classify(req,sparse)).type,"unresolved");
  assert.equal((await classify(req,{...response,job_completion_report:null})).type,"unresolved");
  for (const override of [{summary:"Different"},{fyi_items:[...f.report.fyi_items].reverse().concat("Extra")},{prompt_revision:"4"},{work_item_id:f.followWork},{closeout_status:"promoted"},{actor_session_id:"wrong"}]) assert.equal((await classify(req,{...response,job_completion_report:{...f.report,...override}})).type,"unresolved");
  const legacy=request("complete_work",{expected_version:1,checkpoint:f.checkpointInput});
  assert.equal((await classify(legacy,sparse)).type,"success");
  assert.equal((await classify(legacy,response)).type,"unresolved");
  const retirement=request("update_work",{expected_version:1,status:"wont-do",actor:f.actor,job_completion_report:f.reportInput});
  const retired={...f.workItem,status:"wont-do",job_completion_report:{...f.report,closeout_status:"wont-do",completion_checkpoint_id:null}};
  assert.equal((await classify(retirement,retired)).type,"success");
  assert.equal((await classify(retirement,{...retired,job_completion_report:null})).type,"unresolved");
});

test("dismissal and follow-up receipts validate immutable response identity and exact source", async () => {
  const dismissal={id:f.actionId,...f.actor,created_at:f.timestamp};
  const req=request("dismiss_job_completion_report",{actor:f.actor},{path:`/projects/${f.project}/job-completion-reports/${f.reportId}/dismiss`});
  const result={project_id:f.project,report_id:f.reportId,dismissed:true,human_dismissal:dismissal};
  assert.equal((await classify(req,result)).type,"success");
  assert.equal((await classify(req,{...result,report_id:f.work})).type,"unresolved");
  assert.equal((await classify(req,{...result,human_dismissal:{...dismissal,actor_session_id:"other"}})).type,"unresolved");
  assert.equal((await classify(req,{...result,dismissed:false,human_dismissal:{...dismissal,actor_session_id:"other"}})).type,"success");
  const followReq=request("create_job_completion_report_follow_up",{title:f.workItem.title,summary:f.workItem.summary,priority:5,initial_checkpoint:f.checkpointInput,actor:f.actor},{path:`/projects/${f.project}/job-completion-reports/${f.reportId}/follow-ups`,expectedSourceWorkItemId:f.work});
  const follow={work_item:{...f.workItem,id:f.followWork,status:"pending",version:1},initial_checkpoint:{...f.checkpoint,work_item_id:f.followWork,kind:"context"},follow_up:{id:f.actionId,project_id:f.project,report_id:f.reportId,source_work_item_id:f.work,follow_up_work_item_id:f.followWork,created_sequence:"11",...f.actor,created_at:f.timestamp}};
  assert.equal((await classify(followReq,follow,201)).type,"success");
  assert.equal((await classify(followReq,{...follow,
    initial_checkpoint:{...follow.initial_checkpoint,created_at:"2026-09-05T20:00:00.000002Z"},
    follow_up:{...follow.follow_up,created_at:"2026-09-05T20:00:00.000003Z"}},201)).type,"success");
  assert.equal((await classify(followReq,{...follow,follow_up:{...follow.follow_up,source_work_item_id:f.actionId}},201)).type,"unresolved");
  assert.equal((await classify({...followReq,expectedSourceWorkItemId:undefined},follow,201)).type,"unresolved");
});

test("bounded JSON readers reject large chunked, malformed UTF-8 and misleading length bodies", async () => {
  await assert.rejects(readBoundedJson(new Response('"'+'x'.repeat(100)+'"'),10));
  await assert.rejects(readBoundedJson(new Response(new Uint8Array([0xff])),100));
  await assert.rejects(readBoundedJson(new Response('{}',{headers:{'content-length':'999'}}),100));
  assert.deepEqual(await readBoundedJson(Response.json({ok:true}),100),{ok:true});
});


test("server-assigned report and follow-up timestamps need not equal checkpoint or work timestamps", async () => {
  const req=request("complete_work",
    {expected_version:1,checkpoint:f.checkpointInput,job_completion_report:f.reportInput});
  const response={work_item:{...f.workItem,updated_at:"2026-09-05T20:00:00.000001Z"},
    checkpoint:{...f.checkpoint,created_at:"2026-09-05T20:00:00.000002Z"},
    job_completion_report:{...f.report,created_at:"2026-09-05T20:00:00.000003Z"}};
  assert.equal((await classify(req,response)).type,"success");
});


test("only authored report prose marks a closeout draft dirty", () => {
  const empty = emptyJobReportDraft();
  assert.equal(jobReportDraftHasEdits(empty), false);
  assert.equal(jobReportDraftHasEdits({...empty, promptRevision:"8", fyiItems:["", " \t"]}), false);
  assert.equal(jobReportDraftHasEdits({...empty, summary:"The work is ready to review."}), true);
  assert.equal(jobReportDraftHasEdits({...empty, fyiItems:["", "I chose Arial; this can be changed."]}), true);
});
