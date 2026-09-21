"use client";

/** The public face of the station: pick a language, press Tune in, watch the dial. Everything else -
 *  sources, refusals, hotlines, the schedule - lives behind /admin. */

import Link from "next/link";
import { useEffect, useState } from "react";
import { LANGUAGES, titleCase, useApi } from "@/lib/api";
import type { Station } from "@/lib/types";
import { useStationPlayer } from "@/components/useStationPlayer";
import { Visualizer } from "@/components/Visualizer";

export default function ListenPage() {
  const { data: station } = useApi<Station>("/station");
  const player = useStationPlayer();
  const [size, setSize] = useState(300);

  useEffect(() => {
    const fit = () => setSize(Math.max(180, Math.min(300, window.innerWidth - 72, window.innerHeight - 320)));
    fit();
    window.addEventListener("resize", fit);
    return () => window.removeEventListener("resize", fit);
  }, []);
  const presenter = station?.presenters[player.presenterKey]?.name;

  const status = player.error
    ? player.error
    : player.textOnly
      ? `${LANGUAGES[player.lang]} has no voice yet - read it on the admin side`
      : !player.bulletin
        ? "Finding what is on air…"
        : [
            player.view?.mode === "live" ? "On air" : "Replay of the latest bulletin",
            player.bulletin ? titleCase(player.bulletin.block) : "",
            presenter,
          ]
            .filter(Boolean)
            .join(" · ");

  return (
    <div className="stage">
      <header className="stage-top">
        <h1>RAIA</h1>
        <Link href="/admin" className="admin-link">
          admin
        </Link>
      </header>

      <main className="dial">
        <label className="language">
          <select
            value={player.lang}
            onChange={e => player.setLanguage(e.target.value)}
            aria-label="Select language"
          >
            {Object.entries(LANGUAGES).map(([code, name]) => (
              <option key={code} value={code}>
                {name}
              </option>
            ))}
          </select>
        </label>

        <div className={`dial-face${player.playing ? " on" : ""}`}>
          <Visualizer sources={[...player.audios, ...player.beds]} playing={player.playing} size={size} />
        </div>

        <button
          className="tune"
          onClick={() => (player.playing ? player.stop() : void player.tuneIn())}
          disabled={!player.bulletin && !player.playing}
        >
          {player.playing ? "Stop" : "Tune in"}
        </button>

        <p className="status">{status}</p>
      </main>

      <footer className="stage-foot">
        RAIA&apos;s presenters are synthetic voices made by artificial intelligence. Every story names its sources.
        {player.simulatedTime ? ` · simulated time ${new Date(player.simulatedTime).toLocaleTimeString("en-GB")}` : ""}
      </footer>

      {/* One plays while the other buffers the next segment; the two beds do the same under a replay. */}
      <audio ref={player.audios[0]} preload="auto" crossOrigin="anonymous" onEnded={player.onEnded} />
      <audio ref={player.audios[1]} preload="auto" crossOrigin="anonymous" onEnded={player.onEnded} />
      <audio ref={player.beds[0]} preload="auto" crossOrigin="anonymous" onEnded={player.onBedEnded} />
      <audio ref={player.beds[1]} preload="auto" crossOrigin="anonymous" onEnded={player.onBedEnded} />
    </div>
  );
}
