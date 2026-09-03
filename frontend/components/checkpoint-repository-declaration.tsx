import { useId } from "react";
import type { Checkpoint } from "@/lib/types";

export default function CheckpointRepositoryDeclaration({
  checkpoint
}: {
  checkpoint: Checkpoint;
}) {
  const headingId = useId();
  return <section
    className="checkpoint-repository-declaration"
    aria-labelledby={headingId}
  >
    <h5 id={headingId}>Repository declaration</h5>
    <dl>
      <div>
        <dt>Caller-declared branch</dt>
        <dd className="mono break-all">
          {checkpoint.repository_branch
            ? <bdi dir="auto">{checkpoint.repository_branch}</bdi>
            : "Not declared"}
        </dd>
      </div>
      <div>
        <dt>Caller-asserted baseline</dt>
        <dd className="mono break-all">
          {checkpoint.verified_against
            ? <bdi dir="auto">{checkpoint.verified_against}</bdi>
            : "Not declared"}
        </dd>
      </div>
      <div className="span-two">
        <dt>Declared affected paths</dt>
        <dd>
          {checkpoint.affected_paths.length > 0
            ? <ol className="affected-path-list">
              {checkpoint.affected_paths.map((path, index) => (
                <li key={`${index}:${path}`}><bdi dir="ltr">{path}</bdi></li>
              ))}
            </ol>
            : "No dependency scope declared"}
        </dd>
      </div>
      <div className="span-two">
        <dt>Repository freshness</dt>
        <dd>Not assessed by this browser.</dd>
      </div>
    </dl>
    <p>
      These are caller declarations. They are not proof that the checkpoint is
      current, correct, safe, or independently verified.
    </p>
  </section>;
}
