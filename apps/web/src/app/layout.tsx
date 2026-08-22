import type { Metadata, Viewport } from "next";
import type { ReactNode } from "react";
import "@/styles/app.css";

export const metadata: Metadata = {
  title: "Provenance",
  description: "A system of record for the institutions that already have one of you.",
};

export const viewport: Viewport = {
  width: "device-width",
  initialScale: 1,
};

export default function RootLayout({ children }: { readonly children: ReactNode }) {
  return (
    <html lang="en">
      <body>{children}</body>
    </html>
  );
}
