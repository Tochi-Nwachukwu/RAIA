"use client";

/** About: the disclosure, and the models the station runs on. */

import { useApi } from "@/lib/api";
import type { Station } from "@/lib/types";
import { ErrorNote, Loading } from "@/components/Chip";

export default function AboutPage() {
  const { data: station, error } = useApi<Station>("/station");
  const used = station?.models_used
    ? Object.entries(station.models_used.calls)
        .sort((a, b) => b[1].calls - a[1].calls)
        .map(([model, u]) => `${model} (${u.calls})`)
    : [];

  return (
    <main>
      <h1>About RAIA</h1>
      <p className="lede">
        RAIA - Radio AI Africa - is a civic radio station produced by AI agents and delivered as a scheduled broadcast.
        Agents gather news and sport from named sources, check claims against each other, write spoken copy, translate
        it, and render it to audio ahead of time.
      </p>
      <ErrorNote error={error} />
      {!station && !error ? <Loading /> : null}
      {station ? (
        <>
          <section className="panel">
            <h2 style={{ marginTop: 0 }}>The presenters are synthetic</h2>
            <p>{station.disclosure}</p>
            <p>
              We say so on air once an hour, in every language, and it cannot be turned off. Our presenters:{" "}
              {Object.values(station.presenters)
                .map(p => `${p.name} (${p.languages.join(", ")})`)
                .join(", ")}
              .
            </p>
          </section>

          <h2>The models we use</h2>
          <section className="panel">
            <table>
              <tbody>
                <tr>
                  <th>Job</th>
                  <th>Model</th>
                </tr>
                <tr>
                  <td>High-volume extraction</td>
                  <td>{station.models.cheap?.join(", ")}</td>
                </tr>
                <tr>
                  <td>Scripts, continuity, listener desk</td>
                  <td>{station.models.mid?.join(", ")}</td>
                </tr>
                <tr>
                  <td>Verification, editing, translation, safety review</td>
                  <td>{station.models.expensive?.join(", or when it is unavailable ")}</td>
                </tr>
                <tr>
                  <td>Grouping reports of the same event</td>
                  <td>{station.embeddings}</td>
                </tr>
              </tbody>
            </table>
            <p className="src">
              Every language-model call goes through one gateway in the code; the provider (now: {station.llm_provider})
              and the model for each job are configuration.
            </p>
            {used.length ? (
              <p className="src">
                Model calls recorded for {station.models_used!.date}: {used.join(", ")}.
              </p>
            ) : null}
          </section>

          <h2>Voices</h2>
          <section className="panel">
            <table>
              <tbody>
                <tr>
                  <th>Language</th>
                  <th>Voice model</th>
                  <th />
                </tr>
                {Object.entries(station.tts).map(([code, t]) => (
                  <tr key={code}>
                    <td>{station.languages[code] || code}</td>
                    <td>{t.engine === "yarngpt" ? t.voice_model : "No voice yet"}</td>
                    <td className="src">{t.note || ""}</td>
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
