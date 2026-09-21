"use client";

/** Hotlines: only numbers confirmed on an official page, with the date they were checked. */

import { useApi } from "@/lib/api";
import type { Hotline } from "@/lib/types";
import { ErrorNote, Loading } from "@/components/Chip";

export default function HotlinesPage() {
  const { data, error } = useApi<{ on_air: Hotline[]; not_aired: { service: string; reason: string }[] }>("/hotlines");

  return (
    <main>
      <h1>Numbers to keep close</h1>
      <p className="lede">
        We never invent a number. Each one below was found on the agency&apos;s own website (or, where that site blocks
        checks, a federal broadcaster quoting the agency) on the date shown.
      </p>
      <ErrorNote error={error} />
      {!data && !error ? <Loading what="hotlines" /> : null}
      {data ? (
        <section className="panel">
          <table>
            <tbody>
              <tr>
                <th>Service</th>
                <th>Number</th>
                <th>For</th>
                <th>Checked</th>
              </tr>
              {data.on_air.map(h => (
                <tr key={h.id}>
                  <td>{h.service}</td>
                  <td>
                    <strong>
                      {h.numbers.map((n, i) => (
                        <span key={n}>
                          {i ? <br /> : null}
                          {n}
                        </span>
                      ))}
                    </strong>
                  </td>
                  <td>
                    {h.purpose}
                    {h.hours ? (
                      <>
                        <br />
                        <span className="src">{h.hours}</span>
                      </>
                    ) : null}
                    {h.note ? (
                      <>
                        <br />
                        <span className="src">{h.note}</span>
                      </>
                    ) : null}
                  </td>
                  <td>
                    <a href={h.source_url} target="_blank" rel="noopener">
                      {h.verified_on}
                    </a>
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
          <p className="src">
            Not read on air until confirmed: {data.not_aired.map(n => `${n.service} (${n.reason})`).join("; ")}.
          </p>
        </section>
      ) : null}
    </main>
  );
}
