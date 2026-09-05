import assert from "node:assert/strict";
import test from "node:test";
import { decodeActivityItem, decodeActivityPage, activityInvalidations } from "../lib/project-activity.ts";
import { decodePhase12Cursor } from "../lib/activity-cursors.ts";
import * as f from "./phase12-fixtures.mjs";
const page=(overrides={})=>({project_id:f.project,stream_id:f.stream,items:[f.activity()],next_cursor:f.cursor({kind:"activity",after:"9"}),has_more:false,through_sequence:"9",historical_through_sequence:"0",historical_coverage:"recorded_work_events_only",...overrides});

test("activity consumes every contiguous sequence with an exact discriminated reference matrix",()=>{
  const request={after:f.cursor({kind:"activity",after:"8"})};
  assert.equal(decodeActivityPage(page(),f.project,request).items.length,1);
  assert.deepEqual(activityInvalidations([f.activity()]),{work:false,reports:true,settings:false,projects:false});
  for (const entry of [f.activity({kind:"work_event"}),f.activity({lease_generation_id:f.actionId}),f.activity({project_id:f.project}),f.activity({sequence:9}),f.activity({origin:"history_import"}),f.activity({summary:"Hidden prose"})]) assert.throws(()=>decodeActivityItem(entry));
  for (const overrides of [{items:[f.activity({sequence:"8"})]},{items:[f.activity(),f.activity()]},{through_sequence:"10"},{historical_through_sequence:"10"},{next_cursor:f.cursor({kind:"activity",after:"8"})},{stream_id:f.actionId},{items:[],has_more:true}]) assert.throws(()=>decodeActivityPage(page(overrides),f.project,request));
});

test("activity start-now snapshots and caught-up empty pages preserve explicit position",()=>{
  const empty=page({items:[]});
  assert.equal(decodeActivityPage(empty,f.project,{start:"now"}).next_cursor,empty.next_cursor);
  assert.equal(decodeActivityPage(empty,f.project,{after:empty.next_cursor}).next_cursor,empty.next_cursor);
  assert.throws(()=>decodeActivityPage(empty,f.project));
  assert.throws(()=>decodeActivityPage(page(),f.project,{start:"now"}));
  const imported=f.activity({kind:"work_event",work_event_id:"1",event_type:"work_created",job_completion_report_id:null,origin:"history_import",sequence:"1"});
  assert.equal(decodeActivityPage(page({items:[imported],historical_through_sequence:"1",through_sequence:"1",next_cursor:f.cursor({kind:"activity",after:"1"})}),f.project).items[0].origin,"history_import");
});

test("cursors reject duplicate keys, unbounded integers, padding, unknown keys and wrong streams",()=>{
  const valid=f.cursor({kind:"activity",after:"9007199254740993"});
  assert.equal(decodePhase12Cursor(valid,f.project,"activity").after,"9007199254740993");
  const raw={v:1,kind:"activity",project_id:f.project,stream_id:f.stream,after:"9"};
  const invalid=[valid+"=", f.cursor({...raw,after:"9223372036854775808"}),f.cursor({...raw,after:9}),f.cursor({...raw,extra:"x"}), Buffer.from(JSON.stringify(raw)).toString("base64url"),Buffer.from('{"after":"9","after":"9"}').toString("base64url")];
  for(const cursor of invalid) assert.throws(()=>decodePhase12Cursor(cursor,f.project,"activity"));
});


test("work lifecycle and identity events refresh current source state in Summaries", () => {
  for (const event_type of ["work_updated", "work_reopened", "work_merged", "work_deleted"]) {
    const item=decodeActivityItem(f.activity({kind:"work_event",event_type,work_event_id:"8",job_completion_report_id:null}));
    assert.deepEqual(activityInvalidations([item]),{work:true,reports:true,settings:false,projects:false});
  }
});
