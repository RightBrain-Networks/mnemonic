import type { Checkpoint, WorkContext } from "@/lib/types";

// Recall omits current_context when it is the initial checkpoint so the body is
// serialized once. Every reader resolves it through here.
export function currentContext(context: WorkContext): Checkpoint {
  return context.current_context ?? context.initial_checkpoint;
}
