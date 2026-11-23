import type { Metadata, Viewport } from "next";
import "./globals.css";

export const metadata: Metadata = { title: "Govator Assistant (widget demo)", description: "Demo host page for the Govator chat widget" };
export const viewport: Viewport = { width: "device-width", initialScale: 1 };

export default function RootLayout({ children }: { children: React.ReactNode }) {
  return (
    <html lang="en">
      <body>{children}</body>
    </html>
  );
}
