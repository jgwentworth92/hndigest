import type { Metadata } from "next";
import { WebSocketProvider } from "@/hooks/useWebSocket";
import { Nav } from "@/components/Nav";
import "./globals.css";
import { Geist } from "next/font/google";
import { cn } from "@/lib/utils";

const geist = Geist({subsets:['latin'],variable:'--font-sans'});

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
    <html lang="en" className={cn("font-sans", geist.variable)}>
      <body className="bg-gray-50 text-gray-900 min-h-screen">
        <WebSocketProvider>
          <div className="max-w-6xl mx-auto px-4 py-8">
            <Nav />
            {children}
          </div>
        </WebSocketProvider>
      </body>
    </html>
  );
}
