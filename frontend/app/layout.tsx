import type { Metadata } from "next";
import { themeInitializationScript } from "@/lib/theme-preference";
import "./globals.css";

export const metadata: Metadata = {
  title: "Mnemonic — context worth keeping",
  description: "Durable work items and immutable session checkpoints. Keep your agents on the same page."
};

export default function RootLayout({ children }: Readonly<{ children: React.ReactNode }>) {
  return <html lang="en" suppressHydrationWarning>
    <head><script dangerouslySetInnerHTML={{ __html: themeInitializationScript }} /></head>
    <body>{children}</body>
  </html>;
}
