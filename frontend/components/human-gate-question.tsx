"use client";

import { useEffect, useId, useRef, useState, type KeyboardEvent } from "react";
import MarkdownContent from "@/components/markdown-content";
import Link from "@/components/dashboard-link";
import { DecisionStateBadge } from "@/components/knowledge-shared";
import HumanGateResolution from "@/components/human-gate-resolution";
import { humanGateDecisionPath } from "@/lib/human-gates";
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
  const decisionPath = humanGateDecisionPath(gate);
  const questionPath = humanGateDecisionPath(gate, selected);
  const displayedReference = previous && gate.decision ? `${gate.decision.decision_id}-${selected - 1}` : gate.decision?.reference;
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
    {gate.decision && decisionPath && <div className="question-decision-record">
      <Link className="text-link mono" href={previous ? questionPath! : decisionPath} aria-label={`View product decision record ${displayedReference}`}>{displayedReference}</Link>
      <DecisionStateBadge state={previous ? "proposed" : gate.decision.state} />
      <span>Product Decision Record</span>
    </div>}
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
    {questionPath && (previous || gate.status === "resolved") && <p className="question-history-note"><Link href={questionPath}>View question revision {gate.decision!.decision_id}-{selected - 1}</Link></p>}
    {onResolved && <HumanGateResolution gate={gate} disabled={Boolean(previous)} onResolved={onResolved} onRefresh={onRefresh} />}
  </div>;
}
