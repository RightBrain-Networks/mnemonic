# Automatic MCP UUIDs

Starting with application 0.78.0 and plugin 0.45.0, agents can omit the top-level
`client_operation_id` on a fresh protected write or `claim_request_id` on a fresh
claim. The MCP adapter generates a random UUIDv4 before sending the request to
the API. This covers all 17 receipt-protected tools and both claim tools,
including work creation, checkpoints, completion, relationships and direct
artifact mutations. No shell command or preparatory UUID call is needed.

The UUID is generated once per invocation, with no argument-based deduplication
or shared session state. Two identical calls with omitted IDs are two distinct
intents. Explicit IDs retain their existing validation and replay semantics;
explicit null, blank or invalid IDs are rejected, not replaced. Claims continue
to accept existing opaque nonblank request IDs.

The result includes `_meta.mnemonic_generated_ids`, mapping the generated field
name to its UUID, plus a short text block for clients that do not expose result
metadata. The normal `structuredContent` domain response and output schema stay
unchanged. Generated controls are never added to work text, checkpoint metadata,
events, or URLs. Existing API request bodies and receipt journals receive the
same ID fields they already support. There is no database migration.

Keep the returned ID privately with the original exact arguments. An API timeout,
malformed success or other tool error also includes the generated ID. For an
uncertain outcome, make at most one retry by supplying that ID explicitly and
keeping every other argument unchanged, including transcript assertions, force,
duration and omissions. The adapter still dispatches once per invocation. The
existing domain rules determine whether retry is appropriate: a definitive
rejection or storage/integrity stop does not authorize an exact retry.

If the entire MCP response is lost, including its generated ID, **do not repeat
the call with the ID omitted**. That would generate a different intent and could
duplicate the write. Use safe reads and request direction if recovery remains
ambiguous. Neither search nor a new UUID recovers a lost retry key. These tools
advertise `idempotentHint=false` because a call with an omitted ID is not safe to
repeat automatically. Explicit retained IDs still enable receipt replay.

For callers needing an ID before dispatch, `generate_uuid` takes no arguments
and returns `{"uuid":"<UUIDv4>"}`. It runs locally in the MCP adapter, needs no
project or API request, and stores no record. Every call mints a fresh ID; it
does not recover previous IDs or assign native session identity. Use an actual
client-provided session ID for provenance whenever available.

REST callers retain their existing contracts. Backend entity identifiers were
already generated automatically. Prepared local artifact uploads retain the
UUID frozen by `upload_artifact.py prepare` inside the upload intent; the adapter
does not rewrite it or any other nested identifier. The tool catalog is now 58
tools, with the same 17 receipt-protected MCP writes and 24 REST receipt kinds.
