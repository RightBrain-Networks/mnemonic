import assert from "node:assert/strict";
import test from "node:test";
import { ApiError } from "../lib/api.ts";
import { transcriptSearchRead } from "../lib/search-read.ts";
test("temporary transcript contention retries within a bounded cancellable budget", async () => {
  let calls=0; const delays=[]; const signal=new AbortController().signal;
  const result=await transcriptSearchRead(async()=>{if(++calls<3)throw new ApiError("Busy",503,"transcript_search_busy");return 42;},signal,async ms=>{delays.push(ms);});
  assert.equal(result,42); assert.deepEqual(delays,[500,1500]);
  calls=0;
  await assert.rejects(transcriptSearchRead(async()=>{calls++;throw new ApiError("Busy",503,"transcript_search_busy");},signal,async()=>{}));
  assert.equal(calls,5);
});
test("other failures and cancelled searches are never retried", async () => {
  for (const code of ["client_operation_unavailable","search_temporarily_unavailable","transcript_search_capacity"]) {
    let calls=0;
    await assert.rejects(transcriptSearchRead(async()=>{calls++;throw new ApiError("Failed",503,code);},new AbortController().signal,async()=>assert.fail("No retry")));
    assert.equal(calls,1);
  }
  const controller=new AbortController(); controller.abort();
  await assert.rejects(transcriptSearchRead(async()=>assert.fail("No dispatch"),controller.signal));
});
