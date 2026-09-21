// Provenance: every aired claim, the named sources behind it, and where sources disagreed.
import { api, chip, chrome, esc, when } from "./app.js";

chrome("sources.html");
const main = document.getElementById("main");

function claimRow(claim) {
  const sources = claim.sources.map(s => `<a href="${esc(s.url)}" target="_blank" rel="noopener">${esc(s.outlet)}</a> (${when(s.published_at)})`).join(", ");
  const by = claim.attributed_to ? ` <span class="src">- said by ${esc(claim.attributed_to)}</span>` : "";
  return `<li class="${esc(claim.status)}">${esc(claim.text)}${by}<br>${chip(claim.status)} <span class="src">${sources}</span></li>`;
}

function storyBlock(s) {
  const action = s.action_verified
    ? `<p><strong>What you can do:</strong> ${esc(s.action)} <span class="src">(checked against <a href="${esc(s.action_source)}" target="_blank" rel="noopener">its source</a>)</span></p>`
    : `<p class="src">Information only: no action could be checked against a real source.</p>`;
  const contradictions = s.contradictions.map(c => `<p class="src">Sources disagreed on <strong>${esc(c.subject)}</strong>
      (${c.values.map(v => esc(v.value)).join(" vs ")}). We aired <strong>${esc(c.resolved_value)}</strong>: ${esc(c.rule)}.</p>`).join("");
  return `<article class="story" id="${esc(s.id)}">
    <h3>${esc(s.headline)}</h3>
    <div class="meta">${chip(s.confidence)} ${s.sources.length} source${s.sources.length === 1 ? "" : "s"} from ${s.owners.length}
      independent owner${s.owners.length === 1 ? "" : "s"} · ${esc(s.track.replace("_", " "))} · ${esc(s.region)}
      ${s.aired_in.length ? ` · aired in ${s.aired_in.length} bulletin${s.aired_in.length === 1 ? "" : "s"}` : ""}</div>
    <p>${esc(s.summary)}</p>
    <ul class="claims">${s.claims.map(claimRow).join("")}</ul>
    ${contradictions}${action}
  </article>`;
}

async function render() {
  const [stories, contradictions, registry, airtime] = await Promise.all([
    api("/sources/stories"), api("/sources/contradictions"), api("/sources"), api("/sources/airtime")]);
  main.innerHTML = `
    <h1>Every claim, and where it came from</h1>
    <p class="lede">A claim is corroborated only when outlets with different owners carry it. Campaign claims are always
      attributed. When sources disagree on a date, the official source wins, then the most recent report; when they
      disagree on a figure and none is official, we air neither. Here is everything we aired on ${esc(stories.date)}.</p>
    <section class="panel">${stories.stories.map(storyBlock).join("") || "<p class='muted'>Nothing has aired yet.</p>"}</section>

    <h2>Where sources disagreed (${contradictions.count})</h2>
    <section class="panel"><table>
      <tr><th>Story</th><th>What they disagreed on</th><th>What each source said</th><th>What we aired, and why</th></tr>
      ${contradictions.contradictions.map(c => `<tr>
        <td>${esc(c.headline)}</td><td>${esc(c.subject)}</td>
        <td>${c.values.map(v => `<div><strong>${esc(v.value)}</strong> - ${v.sources.length
          ? v.sources.map(s => `<a href="${esc(s.url)}" target="_blank" rel="noopener">${esc(s.outlet)}</a> (${esc(s.tier)}, ${when(s.published_at)})`).join(", ")
          : "settled fact"}</div>`).join("")}</td>
        <td><strong>${esc(c.resolved_value)}</strong><br><span class="src">${esc(c.rule)}</span></td></tr>`).join("")}
    </table></section>

    <h2>Airtime by party</h2>
    <section class="panel">
      <p class="src">Election coverage must be balanced. Every sentence that names a party counts towards that party:
        its share of the measured speech time, by words. An imbalance is a bug, and we show it here rather than hide it.</p>
      <table><tr><th>Bulletin</th><th>Seconds by party</th><th></th></tr>
      ${airtime.bulletins.map(b => `<tr><td>${esc(b.bulletin)}</td>
        <td>${!b.audio_available ? "<span class='muted'>text only, not aired</span>"
          : Object.entries(b.parties).map(([p, s]) => `${esc(p)}: ${Math.round(s)}s`).join(", ") || "<span class='muted'>no party named</span>"}</td>
        <td>${b.imbalance ? '<span class="chip bad">imbalanced</span>' : b.audio_available ? '<span class="chip good">balanced</span>' : ""}</td></tr>`).join("")}
      </table>
    </section>

    <h2>The outlets we read (${registry.sources.length})</h2>
    <section class="panel"><table>
      <tr><th>Outlet</th><th>Tier</th><th>Owner</th><th>Language</th><th>Checked</th></tr>
      ${registry.sources.map(s => `<tr><td><a href="${esc(s.homepage)}" target="_blank" rel="noopener">${esc(s.outlet)}</a></td>
        <td>${esc(s.tier)}</td><td>${esc(s.owner)}</td><td>${esc(s.language)}</td><td>${esc(s.verified_on || "")}</td></tr>`).join("")}
    </table>
    <p class="src">Two outlets under one owner never corroborate each other. Dropped because they failed our checks:
      ${registry.dropped.map(d => `${esc(d.outlet)} (${esc(d.reason.split(";")[0])})`).join("; ")}.</p></section>`;
  if (location.hash) document.getElementById(location.hash.slice(1))?.scrollIntoView();
}

render().catch(err => { main.innerHTML = `<p class="error">${esc(err.message)}</p>`; });
