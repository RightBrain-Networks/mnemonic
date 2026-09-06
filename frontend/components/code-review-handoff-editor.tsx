"use client";

import { useId } from "react";
import {
  validReviewHandoff,
  type CodeReviewHandoff,
  type RepositoryRange,
} from "@/lib/code-reviews";

export function emptyReviewHandoff(
  repositoryUrl?: string | null,
): CodeReviewHandoff {
  return {
    scope: {
      repositories: [
        {
          repository_key: "repository",
          object_format: "sha1",
          base_commit: "",
          head_commit: "",
          ...(repositoryUrl?.startsWith("https://")
            ? { repository_url: repositoryUrl }
            : {}),
        },
      ],
    },
    handoff: {
      change_summary: "",
      decisions: [],
      focus_areas: [],
      traps: [],
      validation_summary: "",
    },
  };
}

export default function CodeReviewHandoffEditor({
  value,
  onChange,
  disabled = false,
}: {
  value: CodeReviewHandoff;
  onChange: (value: CodeReviewHandoff) => void;
  disabled?: boolean;
}) {
  const id = useId();
  function repository(index: number, update: Partial<RepositoryRange>) {
    onChange({
      ...value,
      scope: {
        repositories: value.scope.repositories.map((row, position) => {
          if (position !== index) return row;
          const next = { ...row, ...update };
          if (!next.repository_url) delete next.repository_url;
          if (!next.checkout_path) delete next.checkout_path;
          return next;
        }),
      },
    });
  }
  return (
    <fieldset className="review-handoff-editor" disabled={disabled}>
      <legend>Code review scope and handoff</legend>
      <p className="field-hint">
        Record the complete change using exact Git commit IDs. Reviewers inspect
        the base-to-head tree difference. Mnemonic does not inspect Git or infer
        commits. Handoff notes are available to warm reviewers; cold prompts
        omit them.
      </p>
      {value.scope.repositories.map((row, index) => (
        <section
          className="review-repository"
          key={`${id}-${index}`}
          aria-label={`Repository ${index + 1}`}
        >
          <div className="review-section-heading">
            <h4>Repository {index + 1}</h4>
            {value.scope.repositories.length > 1 && (
              <button
                type="button"
                className="button button-secondary"
                onClick={() =>
                  onChange({
                    ...value,
                    scope: {
                      repositories: value.scope.repositories.filter(
                        (_, position) => position !== index,
                      ),
                    },
                  })
                }
              >
                Remove repository {index + 1}
              </button>
            )}
          </div>
          <label className="field">
            Repository key
            <input
              required
              maxLength={80}
              pattern="[A-Za-z0-9][A-Za-z0-9._-]*"
              value={row.repository_key}
              onChange={(event) =>
                repository(index, { repository_key: event.target.value })
              }
            />
          </label>
          <label className="field">
            Repository URL
            <input
              type="url"
              maxLength={2000}
              value={row.repository_url ?? ""}
              placeholder="https://github.com/owner/repository"
              onChange={(event) =>
                repository(index, { repository_url: event.target.value })
              }
            />
          </label>
          <label className="field">
            Checkout path
            <input
              maxLength={4096}
              value={row.checkout_path ?? ""}
              placeholder="/absolute/path/to/repository"
              onChange={(event) =>
                repository(index, { checkout_path: event.target.value })
              }
            />
            <span className="field-hint">
              Supply a credential-free HTTPS URL or absolute checkout hint. A
              path may differ on the reviewer's machine.
            </span>
          </label>
          <label className="field">
            Git object format
            <select
              value={row.object_format}
              onChange={(event) =>
                repository(index, {
                  object_format: event.target.value as "sha1" | "sha256",
                })
              }
            >
              <option value="sha1">SHA-1 · 40 characters</option>
              <option value="sha256">SHA-256 · 64 characters</option>
            </select>
          </label>
          <div className="review-commit-fields">
            <label className="field">
              Base commit
              <input
                required
                className="mono"
                maxLength={64}
                pattern={
                  row.object_format === "sha1" ? "[a-f0-9]{40}" : "[a-f0-9]{64}"
                }
                value={row.base_commit}
                onChange={(event) =>
                  repository(index, { base_commit: event.target.value })
                }
              />
            </label>
            <label className="field">
              Head commit
              <input
                required
                className="mono"
                maxLength={64}
                pattern={
                  row.object_format === "sha1" ? "[a-f0-9]{40}" : "[a-f0-9]{64}"
                }
                value={row.head_commit}
                onChange={(event) =>
                  repository(index, { head_commit: event.target.value })
                }
              />
            </label>
          </div>
        </section>
      ))}
      {value.scope.repositories.length < 10 && (
        <button
          type="button"
          className="button button-secondary"
          onClick={() =>
            onChange({
              ...value,
              scope: {
                repositories: [
                  ...value.scope.repositories,
                  {
                    repository_key: `repository-${value.scope.repositories.length + 1}`,
                    object_format: "sha1",
                    base_commit: "",
                    head_commit: "",
                  },
                ],
              },
            })
          }
        >
          Add repository
        </button>
      )}
      <label className="field">
        Change summary
        <textarea
          required
          rows={3}
          maxLength={4000}
          value={value.handoff.change_summary}
          onChange={(event) =>
            onChange({
              ...value,
              handoff: { ...value.handoff, change_summary: event.target.value },
            })
          }
        />
      </label>
      {(
        [
          ["decisions", "Decisions and reasons"],
          ["focus_areas", "Areas of concern"],
          ["traps", "Implementation and testing traps"],
        ] as const
      ).map(([key, label]) => (
        <section key={key}>
          <h4>{label}</h4>
          {value.handoff[key].map((note, index) => (
            <div key={index} className="review-note-entry">
              <label className="field">
                {label} · {index + 1}
                <textarea
                  rows={3}
                  maxLength={2000}
                  value={note}
                  onChange={(event) =>
                    onChange({
                      ...value,
                      handoff: {
                        ...value.handoff,
                        [key]: value.handoff[key].map((text, position) =>
                          position === index ? event.target.value : text,
                        ),
                      },
                    })
                  }
                />
              </label>
              <button
                className="button button-secondary"
                type="button"
                onClick={() =>
                  onChange({
                    ...value,
                    handoff: {
                      ...value.handoff,
                      [key]: value.handoff[key].filter(
                        (_, position) => position !== index,
                      ),
                    },
                  })
                }
              >
                Remove note {index + 1}
              </button>
            </div>
          ))}
          {value.handoff[key].length < 20 && (
            <button
              className="button button-secondary"
              type="button"
              onClick={() =>
                onChange({
                  ...value,
                  handoff: {
                    ...value.handoff,
                    [key]: [...value.handoff[key], ""],
                  },
                })
              }
            >
              Add {label.toLowerCase()} note
            </button>
          )}
          <p className="field-hint">
            Optional. At most 20 notes of 2,000 characters each. Remove unused
            blank notes.
          </p>
        </section>
      ))}
      <label className="field">
        Validation and limitations
        <textarea
          required
          rows={3}
          maxLength={4000}
          value={value.handoff.validation_summary}
          onChange={(event) =>
            onChange({
              ...value,
              handoff: {
                ...value.handoff,
                validation_summary: event.target.value,
              },
            })
          }
        />
        <span className="field-hint">
          Describe checks actually observed and any limitations. Do not paste
          secrets, raw logs or transcripts.
        </span>
      </label>
      {!validReviewHandoff(value) && (
        <p className="field-hint" role="status">
          Complete valid repository ranges, change summary and validation notes
          before submitting.
        </p>
      )}
    </fieldset>
  );
}
