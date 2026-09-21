"use client";

/**
 * The day, drawn like a calendar: 6am to 10pm, a bulletin as a block.
 *
 * Drag a block to move it, drag its bottom edge to change how long it runs, click an empty stretch to
 * put something new there. Minutes and pixels are converted through the grid's own height, so the
 * arithmetic holds at any size; everything snaps to five minutes.
 */

import { useRef, useState } from "react";
import { AUDIO_TONE, clockOf, minutesOf, type Entry } from "@/lib/admin";

const DAY_START = 6 * 60; // the station's first bulletin
const DAY_END = 22 * 60; // and the last
const SNAP = 5;
const MIN_LENGTH = 5;

interface Props {
  entries: Entry[];
  selected: string | null;
  today: boolean;
  onSelect: (id: string) => void;
  onMove: (entry: Entry, startMinutes: number, durationMin: number) => void;
  onCreate: (startMinutes: number) => void;
}

// `from*` is where the block was when the drag began; `start`/`length` are where it is being shown.
type Drag = { id: string; mode: "move" | "resize"; fromY: number; fromStart: number; fromLength: number;
              start: number; length: number; moved: boolean };

const snap = (minutes: number) => Math.round(minutes / SNAP) * SNAP;

/** How ready a bulletin is, which is what its blue says: nothing made, scheduled, being made, done. */
function readiness(entry: Entry): "none" | "scheduled" | "busy" | "done" | "failed" {
  const states = entry.languages.map(language => entry.audio[language]?.state || "none");
  if (states.includes("failed")) return "failed";
  if (states.some(state => state === "queued" || state === "running")) return "busy";
  if (states.length && states.every(state => state === "done")) return "done";
  return states.includes("stale") ? "scheduled" : "none";
}

/** Bulletins that overlap share the width, the way a calendar shows two meetings at once. */
function laneOf(entries: { id: string; start: number; end: number }[]): Map<string, { lane: number; lanes: number }> {
  const placed = new Map<string, { lane: number; lanes: number }>();
  const byStart = [...entries].sort((a, b) => a.start - b.start || a.end - b.end);
  let cluster: typeof byStart = [];
  let clusterEnd = -1;

  const settle = () => {
    const ends: number[] = []; // when each lane is free again
    for (const item of cluster) {
      let lane = ends.findIndex(end => end <= item.start);
      if (lane < 0) lane = ends.length;
      ends[lane] = item.end;
      placed.set(item.id, { lane, lanes: 0 });
    }
    for (const item of cluster) placed.get(item.id)!.lanes = ends.length;
    cluster = [];
  };

  for (const item of byStart) {
    if (cluster.length && item.start >= clusterEnd) settle();
    cluster.push(item);
    clusterEnd = Math.max(clusterEnd, item.end);
  }
  settle();
  return placed;
}

export function Calendar({ entries, selected, today, onSelect, onMove, onCreate }: Props) {
  const body = useRef<HTMLDivElement>(null);
  const [drag, setDrag] = useState<Drag | null>(null);
  const hours = Array.from({ length: (DAY_END - DAY_START) / 60 }, (_, i) => DAY_START / 60 + i);

  const minutesPerPixel = () => {
    const height = body.current?.getBoundingClientRect().height || 1;
    return (DAY_END - DAY_START) / height;
  };
  const topOf = (minutes: number) => `${((minutes - DAY_START) / (DAY_END - DAY_START)) * 100}%`;
  const heightOf = (length: number) => `${(length / (DAY_END - DAY_START)) * 100}%`;

  const onPointerDown = (event: React.PointerEvent, entry: Entry, mode: "move" | "resize") => {
    event.stopPropagation();
    (event.target as HTMLElement).setPointerCapture(event.pointerId);
    onSelect(entry.id);
    const start = minutesOf(entry.start);
    setDrag({ id: entry.id, mode, fromY: event.clientY, fromStart: start, fromLength: entry.duration_min,
              start, length: entry.duration_min, moved: false });
  };

  const onPointerMove = (event: React.PointerEvent) => {
    if (!drag) return;
    // Always measured from where the drag began, never from the last preview, or it would run away.
    const delta = (event.clientY - drag.fromY) * minutesPerPixel();
    if (Math.abs(delta) < 2 && !drag.moved) return;
    setDrag({ ...drag, moved: true, ...preview(drag, delta) });
  };

  const preview = (current: Drag, delta: number) => {
    if (current.mode === "move") {
      const start = Math.min(DAY_END - current.fromLength, Math.max(DAY_START, snap(current.fromStart + delta)));
      return { start, length: current.fromLength };
    }
    const length = Math.max(MIN_LENGTH, Math.min(DAY_END - current.fromStart, snap(current.fromLength + delta)));
    return { start: current.fromStart, length };
  };

  const onPointerUp = () => {
    if (!drag) return;
    const entry = entries.find(e => e.id === drag.id);
    if (entry && drag.moved && (drag.fromStart !== drag.start || drag.fromLength !== drag.length)) {
      onMove(entry, drag.start, drag.length);
    }
    setDrag(null);
  };

  const onBackgroundClick = (event: React.MouseEvent) => {
    if (drag) return;
    const box = body.current?.getBoundingClientRect();
    if (!box) return;
    const minutes = DAY_START + (event.clientY - box.top) * ((DAY_END - DAY_START) / box.height);
    onCreate(Math.max(DAY_START, Math.min(DAY_END - 15, Math.round(minutes / 15) * 15)));
  };

  const nowMinutes = new Date().getHours() * 60 + new Date().getMinutes();
  const lanes = laneOf(
    entries.map(e => {
      const dragging = drag?.id === e.id && drag.moved;
      const start = dragging ? drag!.start : minutesOf(e.start);
      return { id: e.id, start, end: start + (dragging ? drag!.length : e.duration_min) };
    }),
  );

  return (
    <div className="cal">
      <div className="cal-times">
        {hours.map(hour => (
          <div className="cal-time" key={hour}>
            {hour % 12 === 0 ? 12 : hour % 12} {hour < 12 ? "AM" : "PM"}
          </div>
        ))}
      </div>
      <div
        className="cal-body"
        ref={body}
        onClick={onBackgroundClick}
        onPointerMove={onPointerMove}
        onPointerUp={onPointerUp}
        onPointerCancel={onPointerUp}
      >
        {hours.map(hour => (
          <div className="cal-row" key={hour} />
        ))}

        {today && nowMinutes >= DAY_START && nowMinutes <= DAY_END ? (
          <div className="cal-now" style={{ top: topOf(nowMinutes) }} aria-label="now" />
        ) : null}

        {entries.map(entry => {
          const dragging = drag?.id === entry.id && drag.moved;
          const start = dragging ? drag!.start : minutesOf(entry.start);
          const length = dragging ? drag!.length : entry.duration_min;
          const { lane, lanes: columns } = lanes.get(entry.id) || { lane: 0, lanes: 1 };
          const compact = length < 35; // too short to hold three lines
          return (
            <div
              key={entry.id}
              className={`cal-entry${selected === entry.id ? " selected" : ""}${dragging ? " dragging" : ""}${compact ? " compact" : ""}`}
              data-state={readiness(entry)}
              style={{
                top: topOf(start),
                height: heightOf(length),
                left: `calc(8px + ${(100 * lane) / columns}%)`,
                width: `calc(${100 / columns}% - 16px)`,
                right: "auto",
                zIndex: dragging ? 5 : 1 + lane,
              }}
              onPointerDown={e => onPointerDown(e, entry, "move")}
              onClick={e => {
                e.stopPropagation();
                onSelect(entry.id);
              }}
              title={`${entry.title} · ${clockOf(start)}-${clockOf(start + length)}`}
            >
              {compact ? (
                <strong>
                  {entry.title} <span className="when">{clockOf(start)}</span>
                </strong>
              ) : (
                <>
                  <strong>{entry.title}</strong>
                  <span className="when">
                    {clockOf(start)} - {clockOf(start + length)}
                  </span>
                  <div className="langs">
                    {entry.languages.map(language => (
                      <span key={language} className={`lang-dot ${AUDIO_TONE[entry.audio[language]?.state || "none"]}`}>
                        {language}
                        {entry.audio[language]?.state === "done" ? " ✓" : ""}
                      </span>
                    ))}
                  </div>
                </>
              )}
              <div className="cal-handle" onPointerDown={e => onPointerDown(e, entry, "resize")} />
            </div>
          );
        })}
      </div>
    </div>
  );
}
