"use client";

import { useEffect, useId, useRef, useState, type KeyboardEvent } from "react";
import MarkdownContent from "@/components/markdown-content";
import HumanGateResolution from "@/components/human-gate-resolution";
import type { HumanGateRead } from "@/lib/types";

export default function HumanGateQuestion({ gate, onResolved, onRefresh }: {
  gate: HumanGateRead;
  onResolved?: () => void | Promise<void>;
  onRefresh?: () => void | Promise<void>;
}) {
  const id = useId();
  const tabsRef = useRef<HTMLDivElement>(null);
  useEffect(() => {
    const tabs = tabsRef.current;
    if (tabs) tabs.scrollLeft = tabs.scrollWidth;
  }, [gate.question_version]);
  const [selection, setSelection] = useState<{ head: number; version: number } | null>(null);
  const selected = selection?.head === gate.question_version ? selection.version : gate.question_version;
  const previous = gate.previous_questions.find((version) => version.version === selected);
  const versions = [...gate.previous_questions.map((version) => version.version), gate.question_version];
  function select(version: number): void {
    setSelection({ head: gate.question_version, version });
  }
  function navigate(event: KeyboardEvent<HTMLButtonElement>, index: number): void {
    const next = event.key === "ArrowRight" ? (index + 1) % versions.length
      : event.key === "ArrowLeft" ? (index + versions.length - 1) % versions.length
      : event.key === "Home" ? 0 : event.key === "End" ? versions.length - 1 : null;
    if (next === null) return;
    event.preventDefault();
    select(versions[next]);
    document.getElementById(`${id}-tab-${versions[next]}`)?.focus();
  }
  return <div className="question-versions">
    <div ref={tabsRef} className="question-version-tabs" role="tablist" aria-label="Question versions">
      {versions.map((version, index) => <button
        key={version} type="button" role="tab" id={`${id}-tab-${version}`}
        aria-selected={selected === version} aria-controls={`${id}-panel`}
        tabIndex={selected === version ? 0 : -1}
        onClick={() => select(version)} onKeyDown={(event) => navigate(event, index)}
      >Version {version}{version === gate.question_version ? " · Current" : ""}</button>)}
    </div>
    <div id={`${id}-panel`} role="tabpanel" aria-labelledby={`${id}-tab-${selected}`} tabIndex={0}>
      <MarkdownContent className="attention-question">{previous?.question ?? gate.question}</MarkdownContent>
    </div>
    {previous && <p className="question-history-note">You’re viewing an earlier question. <button type="button" className="text-link" onClick={() => select(gate.question_version)}>Return to the current version</button></p>}
    {onResolved && <HumanGateResolution gate={gate} disabled={Boolean(previous)} onResolved={onResolved} onRefresh={onRefresh} />}
  </div>;
}
