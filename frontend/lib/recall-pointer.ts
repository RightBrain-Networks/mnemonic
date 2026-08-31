type HandoffSummary = {
  id: string;
  project_id: string;
  title: string;
};

export function recallPointer(handoff: HandoffSummary): string {
  return `Recall the Mnemonic hand-off "${handoff.title}" (project_id ${handoff.project_id}, handoff_id ${handoff.id}) using recall_handoff, then summarise it and wait for my direction.`;
}
