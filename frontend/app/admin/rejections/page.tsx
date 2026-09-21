"use client";

/** The rejection log: stories and scripts the station refused to air, and why. */

import { useApi } from "@/lib/api";
import type { Rejection } from "@/lib/types";
import { ErrorNote, Loading } from "@/components/Chip";

const STAGE: Record<string, string> = {
  editor: "Editor",
  gate_rules: "Safety gate (rules)",
  gate_review: "Safety gate (review)",
  human_review: "Human review",
  tease_check: "Continuity check",
};

export default function RejectionsPage() {
  const { data, error } = useApi<{ date: string; stories_rejected: number; rejections: Rejection[] }>(
    "/sources/rejections",
  );
  const rows = data ? [...data.rejections].sort((a, b) => a.rule.localeCompare(b.rule)) : [];

  return (
    <main>
      <ErrorNote error={error} />
      {!data && !error ? <Loading what="the rejection log" /> : null}
      {data ? (
        <>
          <h1>{data.stories_rejected} stories we refused to air</h1>
          <p className="lede">
            A station that has thought about when not to broadcast is a serious one. Sensitive stories wait for a human
            editor - there is no override switch. Stories we could not verify do not air. Every script passes a final
            safety gate. This is the full list for {data.date}.
          </p>
          <section className="panel">
            <table>
              <tbody>
                <tr>
                  <th>Story</th>
                  <th>Refused by</th>
                  <th>Rule</th>
                  <th>Why</th>
                </tr>
                {rows.map((r, i) => (
                  <tr key={i}>
                    <td>{r.headline || r.segment_id || ""}</td>
                    <td>{STAGE[r.stage] || r.stage}</td>
                    <td>{r.rule}</td>
                    <td className="src">{r.reason}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </section>
        </>
      ) : null}
    </main>
  );
}
