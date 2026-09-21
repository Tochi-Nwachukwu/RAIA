"use client";

import { useRouter } from "next/navigation";
import { useState } from "react";
import { signIn } from "@/lib/admin";

export default function LoginPage() {
  const router = useRouter();
  const [username, setUsername] = useState("test");
  const [password, setPassword] = useState("");
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);

  const submit = async (event: React.FormEvent) => {
    event.preventDefault();
    setBusy(true);
    setError(null);
    try {
      await signIn(username, password);
      router.push("/admin");
    } catch (err) {
      setError((err as Error).message);
    } finally {
      setBusy(false);
    }
  };

  return (
    <main style={{ maxWidth: 420 }}>
      <h1>Newsroom</h1>
      <p className="lede">Sign in to edit the day&apos;s programme and run the agents.</p>
      <form className="panel" onSubmit={submit}>
        <label className="field">
          <span>Username</span>
          <input value={username} onChange={e => setUsername(e.target.value)} autoComplete="username" />
        </label>
        <label className="field">
          <span>Password</span>
          <input
            type="password"
            value={password}
            onChange={e => setPassword(e.target.value)}
            autoComplete="current-password"
          />
        </label>
        {error ? <p className="error">{error}</p> : null}
        <button className="small go" type="submit" disabled={busy}>
          {busy ? "Signing in…" : "Sign in"}
        </button>
        <p className="src" style={{ marginBottom: 0 }}>
          Demo station: <code>test</code> / <code>password1</code>. Change them in <code>backend/.env</code>
          (<code>ADMIN_USERNAME</code>, <code>ADMIN_PASSWORD</code>) before this is anything but a demo.
        </p>
      </form>
    </main>
  );
}
