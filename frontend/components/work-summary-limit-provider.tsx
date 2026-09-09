"use client";

import type { ReactNode } from "react";
import { WorkSummaryLimitContext } from "@/components/work-summary-input";

export default function WorkSummaryLimitProvider({ maximum, children }: { maximum: number; children: ReactNode }) {
  return <WorkSummaryLimitContext value={maximum}>{children}</WorkSummaryLimitContext>;
}
