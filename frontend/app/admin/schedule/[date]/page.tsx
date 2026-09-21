"use client";

/** One day of programming, drawn as a calendar. Move a bulletin, add one, hand it to the agents. */

import Link from "next/link";
import { useParams, useRouter } from "next/navigation";
import { useEffect, useMemo, useState } from "react";
import { adminApi, isoDay, minutesOf, shiftDay, useAdmin, type Entry, type Job, type Options } from "@/lib/admin";
import { Calendar } from "@/components/admin/Calendar";
import { CreateDialog } from "@/components/admin/CreateDialog";
import { EntryPanel } from "@/components/admin/EntryPanel";
import { ErrorNote, Loading } from "@/components/Chip";

/** The station's own lines, not the libraries' warnings and file paths. */
function said(log: string[]): string {
  const mine = log.filter(line => /^\[\d{2}:\d{2}:\d{2}\]|^run /.test(line));
  return (mine[mine.length - 1] || log[log.length - 1] || "").replace(/^\[[\d:]+\]\s*/, "").slice(0, 90);
}

function minutesSince(iso: string): string {
  const minutes = Math.max(0, Math.round((Date.now() - Date.parse(iso)) / 60000));
  return minutes < 1 ? "just started" : minutes < 60 ? `${minutes} min so far` : `${Math.floor(minutes / 60)}h ${minutes % 60}m so far`;
}

export default function SchedulePage() {
  const date = String(useParams().date);
  const router = useRouter();
  const [selected, setSelected] = useState<string | null>(null);
  const [creatingAt, setCreatingAt] = useState<number | null>(null);
  const [error, setError] = useState<string | null>(null);

  const day = useAdmin<{ date: string; status: string; entries: Entry[]; busy: boolean }>(
    `/days/${date}/entries`,
    8000,
  );
  const options = useAdmin<Options>("/options");
  const jobs = useAdmin<{ busy: boolean; jobs: Job[] }>(`/jobs?date=${date}&limit=5`, 4000);

  const running = jobs.data?.jobs.find(j => j.state === "running" || j.state === "queued") || null;
  const entries = useMemo(
    () => [...(day.data?.entries || [])].sort((a, b) => minutesOf(a.start) - minutesOf(b.start)),
    [day.data],
  );
  const entry = entries.find(e => e.id === selected) || null;
  const today = isoDay() === date;

  // While the agents are working, keep the schedule fresh so audio turns green as it lands.
  useEffect(() => {
    if (!running) return;
    const timer = setInterval(day.reload, 5000);
    return () => clearInterval(timer);
  }, [running, day.reload]); // eslint-disable-line react-hooks/exhaustive-deps

  const move = async (moved: Entry, start: number, duration: number) => {
    setError(null);
    const clock = `${String(Math.floor(start / 60)).padStart(2, "0")}:${String(start % 60).padStart(2, "0")}:00`;
    await adminApi(`/entries/${moved.id}`, {
      method: "PATCH",
      body: JSON.stringify({ start: clock, duration_min: duration }),
    }).catch(err => setError((err as Error).message));
    day.reload();
  };

  const buildDay = async () => {
    setError(null);
    await adminApi(`/days/${date}/build`, { method: "POST" }).catch(err => setError((err as Error).message));
    jobs.reload();
    day.reload();
  };

  const setStatus = async (status: string) => {
    await adminApi(`/days/${date}`, { method: "PATCH", body: JSON.stringify({ status }) }).catch(() => {});
    day.reload();
  };

  return (
    <main>
      <div className="toolbar">
        <button className="small" onClick={() => router.push(`/admin/schedule/${shiftDay(date, -1)}`)}>
          ‹
        </button>
        <strong style={{ fontSize: 18 }}>
          {new Date(date + "T00:00:00").toLocaleDateString(undefined, { weekday: "long", day: "numeric", month: "long" })}
        </strong>
        <button className="small" onClick={() => router.push(`/admin/schedule/${shiftDay(date, 1)}`)}>
          ›
        </button>
        {today ? <span className="chip good">today</span> : null}
        <div className="spacer" />
        <select value={day.data?.status || "open"} onChange={e => setStatus(e.target.value)} aria-label="day status">
          {["open", "review", "complete"].map(status => (
            <option key={status} value={status}>
              {status}
            </option>
          ))}
        </select>
        <button className="small go" onClick={buildDay} disabled={Boolean(running)}>
          Build the whole day
        </button>
        <Link href="/admin" className="small" style={{ textDecoration: "none", padding: "6px 14px" }}>
          All days
        </Link>
      </div>

      {running ? (
        <div className="jobbar">
          <span className="chip warn">{running.state}</span>
          <span>
            {running.kind === "day" ? "Building the whole day" : `Generating ${running.block}`}
            {running.block && running.kind === "day" ? ` · ${running.block}` : ""}
            {running.step ? ` · ${running.step}` : ""} · {minutesSince(running.started_at)}
          </span>
          <code>{said(running.log)}</code>
          <div className="spacer" />
          <button
            className="small"
            onClick={async () => {
              await adminApi(`/jobs/${running.id}/cancel`, { method: "POST" }).catch(() => {});
              jobs.reload();
            }}
          >
            Stop
          </button>
        </div>
      ) : null}

      <ErrorNote error={error || day.error} />
      {day.loading && !day.data ? <Loading what="the day" /> : null}

      <div className="workspace">
        <div>
          <Calendar
            entries={entries}
            selected={selected}
            today={today}
            onSelect={setSelected}
            onMove={move}
            onCreate={minutes => setCreatingAt(minutes)}
          />
          <p className="src" style={{ marginTop: 10 }}>
            Drag a bulletin to move it, drag its bottom edge to change how long it runs, or click an empty stretch to add
            one. The agents build the whole day overnight at {options.data?.nightly_build_at || "00:30"}.
          </p>
        </div>

        {entry ? (
          <EntryPanel
            key={entry.id}
            entry={entry}
            options={options.data}
            busy={Boolean(running)}
            onChanged={() => {
              day.reload();
              jobs.reload();
            }}
            onClosed={() => setSelected(null)}
          />
        ) : (
          <aside className="panel">
            <h2 style={{ marginTop: 0 }}>Nothing selected</h2>
            <p className="src">
              Pick a bulletin to see its languages, its script, and the audio the agents made - or click an empty stretch
              of the day to add one.
            </p>
            {jobs.data?.jobs.length ? (
              <>
                <h2>Recent runs</h2>
                <table>
                  <tbody>
                    {jobs.data.jobs.map(job => (
                      <tr key={job.id}>
                        <td>{job.block || "whole day"}</td>
                        <td>
                          <span className={`chip ${job.state === "done" ? "good" : job.state === "failed" ? "bad" : "warn"}`}>
                            {job.state}
                          </span>
                        </td>
                        <td className="src">{new Date(job.started_at).toLocaleTimeString()}</td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </>
            ) : null}
          </aside>
        )}
      </div>

      {creatingAt !== null ? (
        <CreateDialog
          date={date}
          startMinutes={creatingAt}
          options={options.data}
          onClose={() => setCreatingAt(null)}
          onCreated={id => {
            setCreatingAt(null);
            setSelected(id);
            day.reload();
          }}
        />
      ) : null}
    </main>
  );
}
