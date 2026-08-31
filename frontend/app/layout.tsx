import type { Metadata } from "next";
import "./globals.css";

export const metadata: Metadata = {
  title: "Mnemonic — context worth keeping",
  description: "A durable home for hand-off prompts. Keep the context, pick up the work."
};

export default function RootLayout({ children }: Readonly<{ children: React.ReactNode }>) {
  return <html lang="en"><body>{children}</body></html>;
}
