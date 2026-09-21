import { STATUS } from "@/lib/api";

export function Chip({ status }: { status: string | null | undefined }) {
  if (!status) return null;
  const [tone, label] = STATUS[status] || ["", status];
  return <span className={`chip ${tone}`}>{label}</span>;
}

export function ErrorNote({ error }: { error: string | null }) {
  if (!error) return null;
  return <p className="error">{error}</p>;
}

export function Loading({ what = "" }: { what?: string }) {
  return <p className="muted">Loading{what ? ` ${what}` : ""}…</p>;
}
