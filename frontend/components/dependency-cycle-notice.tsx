import { precedenceEndpoints, type DependencyIssue } from "@/lib/dependency-cycles";

export default function DependencyCycleNotice({ issue }: { issue: DependencyIssue }) {
  const deadlocked = issue.kind === "deadlock";
  return <section className={`dependency-cycle-notice ${deadlocked ? "deadlocked" : "planning-cycle"}`} aria-label={deadlocked ? "Dependency deadlock" : "Planning cycle"}>
    <strong>{deadlocked ? "Dependency deadlock" : "Planning cycle"}</strong>
    <p>{deadlocked
      ? `${issue.role === "cycle_member" ? "This work is on" : "This work depends on"} a dependency cycle. It cannot become ready or complete until the owner repairs the graph.`
      : "These relationships form a cycle if every parent must wait for its children. At least one parent link is descriptive, so this warning does not prevent work. Ask the owner to confirm the intended ordering."}</p>
    <details><summary>Show cycle ({issue.cycle_length} relationships)</summary>
      <ol>{issue.cycle.map((step, index) => {
        const [before, after] = precedenceEndpoints(step);
        return <li key={`${step.relationship_id}:${index}`}><code>{before}</code><span>{step.relationship_type === "blocks" ? "blocks" : "child precedes parent"}</span><code>{after}</code></li>;
      })}</ol>
      {issue.cycle_length > issue.cycle.length && <p>Showing the first {issue.cycle.length} of {issue.cycle_length} relationships. Follow the last item’s relationships to inspect the rest.</p>}
      <small>Cycle reference: <code>{issue.cycle_id}</code></small>
    </details>
  </section>;
}
