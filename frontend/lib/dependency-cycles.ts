import { exactKeys, finiteInteger, objectValue, validUuid } from "./wire-guards.ts";

export interface PrecedenceStep {
  relationship_id: string | null;
  relationship_type: "blocks" | "parent-child";
  source_work_item_id: string;
  target_work_item_id: string;
}

export interface DependencyIssue {
  kind: "deadlock" | "planning-cycle";
  role: "cycle_member" | "depends_on_cycle";
  cycle_id: string;
  cycle_length: number;
  cycle: PrecedenceStep[];
}

export function precedenceEndpoints(step: PrecedenceStep): [string, string] {
  return step.relationship_type === "parent-child"
    ? [step.target_work_item_id, step.source_work_item_id]
    : [step.source_work_item_id, step.target_work_item_id];
}

export function decodeDependencyIssue(value: unknown): DependencyIssue {
  const issue = objectValue(value);
  if (!issue || !exactKeys(issue, ["kind", "role", "cycle_id", "cycle_length", "cycle"])
    || !["deadlock", "planning-cycle"].includes(String(issue.kind))
    || !["cycle_member", "depends_on_cycle"].includes(String(issue.role))
    || typeof issue.cycle_id !== "string" || !/^[0-9a-f]{64}$/.test(issue.cycle_id)
    || !finiteInteger(issue.cycle_length, 1) || !Array.isArray(issue.cycle)
    || issue.cycle.length !== Math.min(issue.cycle_length, 32)) {
    throw new Error("Mnemonic returned invalid dependency cycle evidence.");
  }
  const steps = issue.cycle.map((value) => {
    const step = objectValue(value);
    if (!step || !exactKeys(step, ["relationship_id", "relationship_type", "source_work_item_id", "target_work_item_id"])
      || step.relationship_id !== null && !validUuid(step.relationship_id)
      || !["blocks", "parent-child"].includes(String(step.relationship_type))
      || !validUuid(step.source_work_item_id) || !validUuid(step.target_work_item_id)) {
      throw new Error("Mnemonic returned an invalid dependency cycle step.");
    }
    return step as unknown as PrecedenceStep;
  });
  const endpoints = steps.map(precedenceEndpoints);
  if (endpoints.some((step, index) => index > 0 && endpoints[index - 1]![1].toLowerCase() !== step[0].toLowerCase())
    || steps.length === issue.cycle_length && endpoints.at(-1)![1].toLowerCase() !== endpoints[0]![0].toLowerCase()) {
    throw new Error("Mnemonic returned a disconnected dependency cycle path.");
  }
  return { ...issue, cycle: steps } as unknown as DependencyIssue;
}

export function cycleDescription(issue: DependencyIssue): string {
  let result = precedenceEndpoints(issue.cycle[0]!)[0];
  for (const step of issue.cycle) {
    const label = step.relationship_type === "blocks" ? "blocks" : "parent-child (child to parent)";
    result += ` --${label}--> ${precedenceEndpoints(step)[1]}`;
  }
  const omitted = issue.cycle_length - issue.cycle.length;
  return omitted ? `${result}; ${omitted} further edges in cycle ${issue.cycle_id}` : result;
}
