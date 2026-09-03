import { useId, type ChangeEvent } from "react";

type Props = {
  disabled: boolean;
  error?: string;
  name?: string;
  value?: string;
  onChange?: (value: string) => void;
};

export default function AffectedPathsEditor({
  disabled,
  error = "",
  name,
  value,
  onChange
}: Props) {
  const inputId = useId();
  const hintId = `${inputId}-hint`;
  const errorId = `${inputId}-error`;
  return <div className="field affected-paths-editor">
    <label htmlFor={inputId}>Declared affected paths</label>
    <span className="optional">One pattern per line</span>
    <textarea
      id={inputId}
      name={name}
      className="mono"
      disabled={disabled}
      rows={5}
      maxLength={32_831}
      spellCheck={false}
      autoCapitalize="none"
      autoCorrect="off"
      placeholder={"src/**\ntests/test_*.py"}
      aria-describedby={`${hintId}${error ? ` ${errorId}` : ""}`}
      aria-invalid={Boolean(error)}
      {...(value === undefined ? {} : { value })}
      {...(onChange === undefined
        ? {}
        : { onChange: (event: ChangeEvent<HTMLTextAreaElement>) => onChange(event.target.value) })}
    />
    <span className="field-hint" id={hintId}>
      List source dependencies, not merely changed files. Safe ASCII only; <code>*</code>
      matches within one component and <code>**</code> spans components. Empty means no
      dependency scope was declared. A non-empty list requires a caller-asserted baseline
      commit. This browser records the declaration but does not assess a local repository.
    </span>
    {error && <span className="field-error" id={errorId} role="alert">{error}</span>}
  </div>;
}
