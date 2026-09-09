"use client";

import { createContext, useContext, useEffect, useRef, type TextareaHTMLAttributes } from "react";
import { DEFAULT_WORK_SUMMARY_MAX_CHARS, workSummaryValidationMessage } from "@/lib/work-summary-limit";

export const WorkSummaryLimitContext = createContext(DEFAULT_WORK_SUMMARY_MAX_CHARS);

export default function WorkSummaryInput({ unchangedValue, ...props }: TextareaHTMLAttributes<HTMLTextAreaElement> & {
  unchangedValue?: string;
}) {
  const maximum = useContext(WorkSummaryLimitContext);
  const input = useRef<HTMLTextAreaElement>(null);
  function validate(element: HTMLTextAreaElement) {
    element.setCustomValidity(element.value === unchangedValue ? "" : workSummaryValidationMessage(element.value, maximum));
  }
  useEffect(() => { if (input.current) validate(input.current); }, [maximum, props.value, unchangedValue]);
  return <>
    <textarea aria-description={`Maximum ${maximum} characters for a new or changed summary.`} {...props} ref={input} onInput={(event) => {
      validate(event.currentTarget);
      props.onInput?.(event);
    }} />
    <span className="field-hint" aria-hidden="true">Up to {maximum.toLocaleString("en-US")} characters.</span>
  </>;
}
