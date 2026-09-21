"use client";

/** Provenance: every aired claim, the named sources behind it, where sources disagreed, and airtime. */

import { useEffect } from "react";
import { titleCase, useApi, when } from "@/lib/api";
import type { AirtimeBulletin, Claim, SourceRow, Story, StoryContradiction } from "@/lib/types";
import { Chip, ErrorNote, Loading } from "@/components/Chip";

interface Contradiction extends StoryContradiction {
  story_id: string;
  headline: string;
}

function ClaimRow({ claim }: { claim: Claim }) {
  return (
    <li className={claim.status}>
      {claim.text}
      {claim.attributed_to ? <span className="src"> - said by {claim.attributed_to}</span> : null}
      <br />
      <Chip status={claim.status} />{" "}
      <span className="src">
        {claim.sources.map((s, i) => (
          <span key={s.url + i}>
            {i ? ", " : ""}
            <a href={s.url} target="_blank" rel="noopener">
              {s.outlet}
            </a>{" "}
            ({when(s.published_at)})
          </span>
        ))}
      </span>
    </li>
  );
}

function StoryBlock({ story }: { story: Story }) {
  return (
    <article className="story" id={story.id}>
      <h3>{story.headline}</h3>
      <div className="meta">
        <Chip status={story.confidence} /> {story.sources.length} source{story.sources.length === 1 ? "" : "s"} from{" "}
        {story.owners.length} independent owner{story.owners.length === 1 ? "" : "s"} · {titleCase(story.track)} ·{" "}
        {story.region}
        {story.aired_in.length ? ` · aired in ${story.aired_in.length} bulletin${story.aired_in.length === 1 ? "" : "s"}` : ""}
      </div>
      <p>{story.summary}</p>
      <ul className="claims">
        {story.claims.map((claim, i) => (
          <ClaimRow claim={claim} key={i} />
        ))}
      </ul>
      {story.contradictions.map((c, i) => (
        <p className="src" key={i}>
          Sources disagreed on <strong>{c.subject}</strong> ({c.values.map(v => v.value).join(" vs ")}). We aired{" "}
          <strong>{c.resolved_value}</strong>: {c.rule}.
        </p>
      ))}
      {story.action_verified ? (
        <p>
          <strong>What you can do:</strong> {story.action}{" "}
          <span className="src">
            (checked against{" "}
            <a href={story.action_source || "#"} target="_blank" rel="noopener">
              its source
            </a>
            )
          </span>
        </p>
      ) : (
        <p className="src">Information only: no action could be checked against a real source.</p>
      )}
    </article>
  );
}

export default function SourcesPage() {
  const stories = useApi<{ date: string; stories: Story[] }>("/sources/stories");
  const contradictions = useApi<{ count: number; contradictions: Contradiction[] }>("/sources/contradictions");
  const registry = useApi<{ sources: SourceRow[]; dropped: { outlet: string; reason: string }[] }>("/sources");
  const airtime = useApi<{ bulletins: AirtimeBulletin[] }>("/sources/airtime");
  const error = stories.error || contradictions.error || registry.error || airtime.error;

  // Deep links from the player: /sources#<story id>
  useEffect(() => {
    if (stories.data && window.location.hash) {
      document.getElementById(decodeURIComponent(window.location.hash.slice(1)))?.scrollIntoView();
    }
  }, [stories.data]);

  return (
    <main>
      <h1>Every claim, and where it came from</h1>
      <p className="lede">
        A claim is corroborated only when outlets with different owners carry it. Campaign claims are always attributed.
        When sources disagree on a date, the official source wins, then the most recent report; when they disagree on a
        figure and none is official, we air neither. Here is everything we aired
        {stories.data ? ` on ${stories.data.date}` : ""}.
      </p>
      <ErrorNote error={error} />
      {!stories.data && !error ? <Loading what="the day's stories" /> : null}

      {stories.data ? (
        <section className="panel">
          {stories.data.stories.length ? (
            stories.data.stories.map(s => <StoryBlock story={s} key={s.id} />)
          ) : (
            <p className="muted">Nothing has aired yet.</p>
          )}
        </section>
      ) : null}

      {contradictions.data ? (
        <>
          <h2>Where sources disagreed ({contradictions.data.count})</h2>
          <section className="panel">
            <table>
              <tbody>
                <tr>
                  <th>Story</th>
                  <th>What they disagreed on</th>
                  <th>What each source said</th>
                  <th>What we aired, and why</th>
                </tr>
                {contradictions.data.contradictions.map((c, i) => (
                  <tr key={i}>
                    <td>{c.headline}</td>
                    <td>{c.subject}</td>
                    <td>
                      {c.values.map((v, k) => (
                        <div key={k}>
                          <strong>{v.value}</strong> -{" "}
                          {v.sources.length
                            ? v.sources.map((s, j) => (
                                <span key={j}>
                                  {j ? ", " : ""}
                                  <a href={s.url} target="_blank" rel="noopener">
                                    {s.outlet}
                                  </a>{" "}
                                  ({s.tier}, {when(s.published_at)})
                                </span>
                              ))
                            : "settled fact"}
                        </div>
                      ))}
                    </td>
                    <td>
                      <strong>{c.resolved_value}</strong>
                      <br />
                      <span className="src">{c.rule}</span>
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </section>
        </>
      ) : null}

      {airtime.data ? (
        <>
          <h2>Airtime by party</h2>
          <section className="panel">
            <p className="src">
              Election coverage must be balanced. Every sentence that names a party counts towards that party: its share
              of the measured speech time, by words. An imbalance is a bug, and we show it here rather than hide it.
            </p>
            <table>
              <tbody>
                <tr>
                  <th>Bulletin</th>
                  <th>Seconds by party</th>
                  <th />
                </tr>
                {airtime.data.bulletins.map(b => (
                  <tr key={b.bulletin}>
                    <td>{b.bulletin}</td>
                    <td>
                      {!b.audio_available ? (
                        <span className="muted">text only, not aired</span>
                      ) : Object.keys(b.parties).length ? (
                        Object.entries(b.parties)
                          .map(([party, seconds]) => `${party}: ${Math.round(seconds)}s`)
                          .join(", ")
                      ) : (
                        <span className="muted">no party named</span>
                      )}
                    </td>
                    <td>
                      {b.imbalance ? (
                        <span className="chip bad">imbalanced</span>
                      ) : b.audio_available ? (
                        <span className="chip good">balanced</span>
                      ) : null}
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </section>
        </>
      ) : null}

      {registry.data ? (
        <>
          <h2>The outlets we read ({registry.data.sources.length})</h2>
          <section className="panel">
            <table>
              <tbody>
                <tr>
                  <th>Outlet</th>
                  <th>Tier</th>
                  <th>Owner</th>
                  <th>Language</th>
                  <th>Checked</th>
                </tr>
                {registry.data.sources.map(s => (
                  <tr key={s.outlet + s.homepage}>
                    <td>
                      <a href={s.homepage} target="_blank" rel="noopener">
                        {s.outlet}
                      </a>
                    </td>
                    <td>{s.tier}</td>
                    <td>{s.owner}</td>
                    <td>{s.language}</td>
                    <td>{s.verified_on || ""}</td>
                  </tr>
                ))}
              </tbody>
            </table>
            <p className="src">
              Two outlets under one owner never corroborate each other. Dropped because they failed our checks:{" "}
              {registry.data.dropped.map(d => `${d.outlet} (${d.reason.split(";")[0]})`).join("; ")}.
            </p>
          </section>
        </>
      ) : null}
    </main>
  );
}
