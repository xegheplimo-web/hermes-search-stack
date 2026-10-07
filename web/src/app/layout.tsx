import type { Metadata, Viewport } from "next";
import "./globals.css";

export const metadata: Metadata = {
  title: "Hermes Chat",
  description: "Web UI for the Hermes search + VN-intelligence stack",
};

export const viewport: Viewport = {
  colorScheme: "light dark",
};

export default function RootLayout({
  children,
}: Readonly<{ children: React.ReactNode }>) {
  return (
    <html lang="vi">
      <body className="bg-background text-foreground antialiased">{children}</body>
    </html>
  );
}
