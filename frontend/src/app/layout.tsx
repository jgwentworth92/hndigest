import type { Metadata } from "next";
import "./globals.css";

export const metadata: Metadata = {
  title: "hndigest",
  description: "Hacker News daily digest dashboard",
};

export default function RootLayout({
  children,
}: {
  children: React.ReactNode;
}) {
  return (
    <html lang="en">
      <body className="bg-gray-50 text-gray-900 min-h-screen">
        <main className="max-w-6xl mx-auto px-4 py-8">{children}</main>
      </body>
    </html>
  );
}
