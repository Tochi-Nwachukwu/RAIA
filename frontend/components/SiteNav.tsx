"use client";

import Link from "next/link";
import { usePathname } from "next/navigation";

const PAGES: [string, string][] = [
  ["/admin", "Schedule"],
  ["/admin/messages", "Messages"],
  ["/admin/bulletins", "Bulletins"],
  ["/admin/sources", "Sources"],
  ["/admin/rejections", "What we refused"],
  ["/admin/hotlines", "Hotlines"],
  ["/admin/about", "About"],
];

export function SiteNav() {
  const path = usePathname();
  return (
    <nav className="site">
      {PAGES.map(([href, label]) => (
        <Link key={href} href={href} className={path === href ? "active" : ""}>
          {label}
        </Link>
      ))}
      <Link href="/">On air ↗</Link>
    </nav>
  );
}
