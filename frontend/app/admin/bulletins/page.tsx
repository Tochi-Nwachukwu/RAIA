"use client";

/** Every bulletin of the day, on demand: play one from the top, or any segment, and read along. */

import { Suspense, useEffect, useRef, useState } from "react";
import { useSearchParams } from "next/navigation";
import Link from "next/link";
import { api, KIND, LANGUAGES, minutes, stationClock, titleCase, useApi, friendly } from "@/lib/api";
import type { Bulletin, BulletinSummary, ScheduleEntry, Station } from "@/lib/types";
import { ErrorNote, Loading } from "@/components/Chip";

const ORDER = Object.keys(LANGUAGES);

function BulletinCard({ bulletin, station }: { bulletin: Bulletin; station: Station | null }) {
  const players = useRef<(HTMLAudioElement | null)[]>([]);
  const scripts = Object.fromEntries(bulletin.segments.map(s => [s.id, s]));
  const total = bulletin.schedule.reduce((sum, e) => sum + e.duration, 0);
  const voices = [...new Set(bulletin.segments.map(s => station?.presenters[s.presenter]?.name).filter(Boolean))];

  const playFromTop = () => {
    document.querySelectorAll("audio").forEach(a => a.pause());
    const queue = bulletin.schedule
      .map((entry, i) => ({ entry, element: players.current[i] }))
      .filter(x => x.entry.kind !== "timecheck" && x.element);
    const play = (i: number) => {
      const step = queue[i];
      if (!step?.element) return;
      step.element.currentTime = 0;
      step.element.onended = () => play(i + 1);
      step.element.scrollIntoView({ block: "nearest", behavior: "smooth" });
      void step.element.play().catch(() => {});
    };
    play(0);
  };

  if (!bulletin.audio_available) {
    return (
      <section className="panel bulletin" id={bulletin.id}>
        <h2>
          {titleCase(bulletin.block)} · {LANGUAGES[bulletin.language] || bulletin.language}
        </h2>
        <p className="meta">
          {stationClock(bulletin.starts_at).slice(0, 5)} · text only:{" "}
          {bulletin.tts_note || "no voice for this language yet"}
        </p>
        {bulletin.segments
          .filter(s => s.script)
          .map(s => (
            <div className="seg" key={s.id}>
              <div className="meta">
                <strong className="ink">{KIND[s.kind] || s.kind}</strong>
              </div>
              <p className="script">{s.script}</p>
            </div>
          ))}
      </section>
    );
  }

  return (
    <section className="panel bulletin" id={bulletin.id}>
      <h2>
        {titleCase(bulletin.block)} · {LANGUAGES[bulletin.language] || bulletin.language}
      </h2>
      <p className="meta">
        Aired at {stationClock(bulletin.starts_at).slice(0, 5)} · {minutes(total)}
        {voices.length ? ` · ${voices.join(", ")}` : ""}
      </p>
      <button className="primary" onClick={playFromTop}>
        Play from the top
      </button>
      <ol className="segments">
        {bulletin.schedule.map((entry: ScheduleEntry, i) => {
          const segment = scripts[entry.segment_id];
          const who = entry.kind === "bed" ? "" : station?.presenters[segment?.presenter]?.name || "";
          return (
            <li className="seg" key={entry.segment_id}>
              <div className="meta">
                <strong className="ink">{KIND[entry.kind] || entry.kind}</strong> ·{" "}
                {stationClock(bulletin.starts_at, entry.offset)} · {minutes(entry.duration)}
                {who ? ` · ${who}` : ""}
                {entry.kind === "timecheck" ? " · as aired; left out when played back" : ""}
              </div>
              <audio
                controls
                preload="none"
                src={entry.audio_url}
                ref={element => {
                  players.current[i] = element;
                }}
              />
              {segment?.script ? (
                <details>
                  <summary className="src">Read along</summary>
                  <p className="script">{segment.script}</p>
                </details>
              ) : null}
            </li>
          );
        })}
      </ol>
    </section>
  );
}

export default function BulletinsPage() {
  return (
    <Suspense fallback={<Loading what="bulletins" />}>
      <Bulletins />
    </Suspense>
  );
}

function Bulletins() {
  const lang = useSearchParams().get("lang");
  const { data: list, error } = useApi<BulletinSummary[]>("/bulletins");
  const { data: station } = useApi<Station>("/station");
  const [bulletins, setBulletins] = useState<Bulletin[] | null>(null);
  const [loadError, setLoadError] = useState<string | null>(null);

  useEffect(() => {
    if (!list) return;
    let live = true;
    const chosen = list
      .filter(b => !lang || b.language === lang)
      .sort(
        (a, b) =>
          b.date.localeCompare(a.date) || // newest day first, then the day's own running order
          a.starts_at.localeCompare(b.starts_at) ||
          ORDER.indexOf(a.language) - ORDER.indexOf(b.language),
      );
    Promise.all(chosen.map(b => api<Bulletin>(`/bulletins/${encodeURIComponent(b.id)}`)))
      .then(full => live && setBulletins(full))
      .catch(err => live && setLoadError(friendly(err as Error)));
    return () => {
      live = false;
    };
  }, [list, lang]);

  return (
    <main>
      <h1>Every bulletin, on demand</h1>
      <p className="lede">
        The <Link href="/">Listen</Link> page follows the station clock, like a radio. Here you can play any of the
        day&apos;s bulletins from the top, or any single segment, and read the script along with it. The voices are
        synthetic.
      </p>
      <p className="meta">
        Language: <Link href="/bulletins" className={!lang ? "ink" : ""}>all</Link>
        {ORDER.map(code => (
          <span key={code}>
            {" · "}
            <Link href={`/bulletins?lang=${code}`} className={lang === code ? "ink" : ""}>
              {LANGUAGES[code]}
            </Link>
          </span>
        ))}
      </p>
      <ErrorNote error={error || loadError} />
      {!bulletins && !error && !loadError ? <Loading what="bulletins" /> : null}
      {bulletins?.map((b, i) => (
        <div key={b.id}>
          {/* Runs from several days can be on disk at once; say which day you are listening to. */}
          {i === 0 || b.date !== bulletins[i - 1].date ? (
            <h2 style={{ marginTop: 26 }}>
              {new Date(b.date + "T00:00:00").toLocaleDateString(undefined, { weekday: "long", day: "numeric", month: "long" })}
            </h2>
          ) : null}
          <BulletinCard bulletin={b} station={station} />
        </div>
      ))}
      {bulletins && !bulletins.length ? <p className="muted">No bulletins yet.</p> : null}
    </main>
  );
}
