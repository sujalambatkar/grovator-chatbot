import type { Metadata, Viewport } from "next";
import "./globals.css";

export const metadata: Metadata = { title: "Grovator Assistant (widget demo)", description: "Demo host page for the Grovator chat widget" };
export const viewport: Viewport = { width: "device-width", initialScale: 1 };

export default function RootLayout({ children }: { children: React.ReactNode }) {
  return (
    <html lang="en">
      <body>{children}</body>
    </html>
  );
}
