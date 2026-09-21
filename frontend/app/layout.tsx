import type { Metadata } from "next";
import "./globals.css";

export const metadata: Metadata = {
  title: "RAIA - Radio AI Africa",
  description: "A civic radio station produced by AI agents. Every aired claim traces back to named sources.",
};

export default function RootLayout({ children }: { children: React.ReactNode }) {
  return (
    <html lang="en">
      <body>{children}</body>
    </html>
  );
}
