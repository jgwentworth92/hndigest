"use client";

import Link from "next/link";
import { usePathname } from "next/navigation";
import { buttonVariants } from "@/components/ui/button";
import { cn } from "@/lib/utils";
import { ConnectionStatus } from "./ConnectionStatus";

const links = [
  { href: "/", label: "Digest" },
  { href: "/feed", label: "Live Feed" },
  { href: "/system", label: "System" },
];

export function Nav() {
  const pathname = usePathname();
  return (
    <nav className="flex items-center gap-6 border-b border-gray-200 pb-4 mb-8">
      <span className="text-lg font-bold">hndigest</span>
      <ConnectionStatus />
      <div className="flex gap-1 ml-4">
        {links.map(({ href, label }) => (
          <Link
            key={href}
            href={href}
            className={cn(
              buttonVariants({ variant: "ghost", size: "sm" }),
              pathname === href
                ? "text-blue-600 font-semibold"
                : "text-gray-600",
            )}
          >
            {label}
          </Link>
        ))}
      </div>
    </nav>
  );
}
