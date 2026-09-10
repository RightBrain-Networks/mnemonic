import WorkSummaryLimitProvider from "@/components/work-summary-limit-provider";
import { workSummaryMaxChars } from "@/lib/work-summary-limit";
import type { Metadata } from "next";
import { libraryToolsInitializationScript } from "@/lib/dashboard-preferences";
import { themeInitializationScript } from "@/lib/theme-preference";
import "./globals.css";

export const dynamic = "force-dynamic";

export const metadata: Metadata = {
  title: "Mnemonic — context worth keeping",
  description: "Durable work items and immutable session checkpoints. Keeping your agents on the same page."
};

export default function RootLayout({ children }: Readonly<{ children: React.ReactNode }>) {
  return <html lang="en" suppressHydrationWarning>
    <head>
      <script dangerouslySetInnerHTML={{ __html: themeInitializationScript }} />
      <script dangerouslySetInnerHTML={{ __html: libraryToolsInitializationScript }} />
    </head>
    <body><WorkSummaryLimitProvider maximum={workSummaryMaxChars(process.env.MNEMONIC_WORK_SUMMARY_MAX_CHARS)}>{children}</WorkSummaryLimitProvider></body>
  </html>;
}
