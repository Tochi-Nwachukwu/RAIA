"use client";

import Link from "next/link";
import { usePathname, useRouter } from "next/navigation";
import { useEffect, useState } from "react";
import { setToken, token } from "@/lib/admin";
import { SiteNav } from "@/components/SiteNav";

const DISCLOSURE =
  "RAIA's presenters are synthetic voices made by artificial intelligence. Every story names its sources - check them on the Sources page.";

export default function AdminLayout({ children }: { children: React.ReactNode }) {
  const path = usePathname();
  const router = useRouter();
  const isLogin = path === "/admin/login";
  const [ready, setReady] = useState(false);

  useEffect(() => {
    if (isLogin) return setReady(true);
    if (!token()) router.replace("/admin/login");
    else setReady(true);
  }, [isLogin, router, path]);

  return (
    <div className="newsroom">
      {/* The disclosure is on every page and cannot be dismissed. */}
      <div className="disclosure" role="note">
        {DISCLOSURE}
      </div>
      <header className="site">
        <Link className="brand" href="/admin">
          RAIA <span>newsroom</span>
        </Link>
        {!isLogin ? <SiteNav /> : null}
        {!isLogin && ready ? (
          <button
            className="small"
            onClick={() => {
              setToken(null);
              router.replace("/admin/login");
            }}
          >
            Sign out
          </button>
        ) : null}
      </header>
      {ready ? children : <main><p className="muted">Checking your session…</p></main>}
      <footer className="site">RAIA is a civic radio station produced by AI agents. {DISCLOSURE}</footer>
    </div>
  );
}
