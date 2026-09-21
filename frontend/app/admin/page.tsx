"use client";

/** The newsroom's front door: every day of programming, and how far it has got. */

import Link from "next/link";
import { useRouter } from "next/navigation";
import { useState } from "react";
import { adminApi, useAdmin } from "@/lib/admin";
import { ErrorNote, Loading } from "@/components/Chip";

interface Day {
  date: string;
  status: "open" | "review" | "complete";
  entries: number;
  audio_done: number;
  audio_wanted: number;
  has_run: boolean;
  note: string | null;
}

function longDate(iso: string): string {
  const day = new Date(iso + "T00:00:00");
  const n = day.getDate();
  const suffix = n % 10 === 1 && n !== 11 ? "st" : n % 10 === 2 && n !== 12 ? "nd" : n % 10 === 3 && n !== 13 ? "rd" : "th";
  return `${n}${suffix} ${day.toLocaleString(undefined, { month: "long" })}`;
}

export default function NewsroomDays() {
  const router = useRouter();
  const { data, error, reload } = useAdmin<{ days: Day[]; today: string }>("/days", 15000);
  const [working, setWorking] = useState<string | null>(null);

  const setStatus = async (date: string, status: string) => {
    setWorking(date);
    await adminApi(`/days/${date}`, { method: "PATCH", body: JSON.stringify({ status }) }).catch(() => {});
    setWorking(null);
    reload();
  };

  return (
    <main>
      <div className="toolbar">
        <div className="tabs">
          <span className="on" style={{ padding: "7px 16px", borderRadius: 999, border: "1px solid var(--line)" }}>
            Content
          </span>
          <Link href="/admin/messages">Messages</Link>
        </div>
        <div className="spacer" />
        {data ? <span className="meta">Today is {longDate(data.today)}</span> : null}
      </div>

      <h1>The programme, day by day</h1>
      <p className="lede">
        Overnight the agents read the day&apos;s news, write it, and render every bulletin. Open a day to see its
        schedule, move a bulletin, add one, or send a script back to the agents.
      </p>

      <ErrorNote error={error} />
      {!data && !error ? <Loading what="the diary" /> : null}

      {data ? (
        <section className="panel">
          <table>
            <tbody>
              <tr>
                <th>Day</th>
                <th>Programme</th>
                <th>Audio</th>
                <th>Status</th>
                <th />
              </tr>
              {data.days.map(day => (
                <tr key={day.date}>
                  <td>
                    <strong>{longDate(day.date)}</strong>
                    <br />
                    <span className="src">{day.date}</span>
                    {day.date === data.today ? <span className="chip good" style={{ marginLeft: 8 }}>today</span> : null}
                  </td>
                  <td>
                    {day.entries ? `${day.entries} bulletins` : <span className="muted">not opened yet</span>}
                    {day.has_run ? <div className="src">stories gathered</div> : null}
                  </td>
                  <td>
                    {day.audio_wanted ? (
                      <span className={`chip ${day.audio_done === day.audio_wanted ? "good" : day.audio_done ? "warn" : ""}`}>
                        {day.audio_done} of {day.audio_wanted} done
                      </span>
                    ) : (
                      <span className="muted">-</span>
                    )}
                  </td>
                  <td>
                    <select
                      value={day.status}
                      disabled={working === day.date}
                      onChange={e => setStatus(day.date, e.target.value)}
                      aria-label={`status for ${day.date}`}
                    >
                      {["open", "review", "complete"].map(status => (
                        <option key={status} value={status}>
                          {status}
                        </option>
                      ))}
                    </select>
                  </td>
                  <td>
                    <button className="small go" onClick={() => router.push(`/admin/schedule/${day.date}`)}>
                      open day
                    </button>
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </section>
      ) : null}
    </main>
  );
}
