import type { Metadata } from "next";
import "./globals.css";

export const metadata: Metadata = {
  title: "Mnemonic — context worth keeping",
  description: "Durable work items and immutable session checkpoints. Keep your agents on the same page."
};

export default function RootLayout({ children }: Readonly<{ children: React.ReactNode }>) {
  return <html lang="en"><body>{children}</body></html>;
}
