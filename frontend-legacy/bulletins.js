// Every bulletin of the day, on demand: play one from the top, or any single segment, and read along.
// The live player (index.html) follows the station clock; this page is for listening back.
import { api, chrome, esc, LANGUAGES } from "./app.js";

chrome("bulletins.html");
const main = document.getElementById("main");
const params = new URLSearchParams(location.search);
const KIND = { station_id: "Station ID", disclosure: "Disclosure", headlines: "Headlines", story: "Story",
  explainer: "Explainer", sport: "Sport", listener: "Your questions", hotlines: "Hotlines", handover: "Handover",
  tease: "Coming up", timecheck: "Time check", bed: "Music bed" };
const ORDER = Object.keys(LANGUAGES);

const clock = (startsAt, offset) => new Date(Date.parse(startsAt) + offset * 1000)
  .toLocaleTimeString("en-GB", { timeZone: "Africa/Lagos" });
const minutes = s => `${Math.floor(s / 60)}:${String(Math.round(s % 60)).padStart(2, "0")}`;
const title = b => `${b.block.replaceAll("_", " ").replace(/^\w/, c => c.toUpperCase())} · ${LANGUAGES[b.language] || b.language}`;

function segmentRow(b, entry, segment, presenters) {
  const who = entry.kind === "bed" ? "" : presenters[segment?.presenter]?.name || "";
  const note = entry.kind === "timecheck" ? " · as aired; left out when played back" : "";
  return `<li class="seg" data-kind="${esc(entry.kind)}">
    <div class="meta"><strong class="ink">${esc(KIND[entry.kind] || entry.kind)}</strong> · ${clock(b.starts_at, entry.offset)}
      · ${minutes(entry.duration)}${who ? ` · ${esc(who)}` : ""}${note}</div>
    <audio controls preload="none" src="${esc(entry.audio_url)}"></audio>
    ${segment?.script ? `<details><summary class="src">Read along</summary><p class="script">${esc(segment.script)}</p></details>` : ""}
  </li>`;
}

function bulletinCard(b, presenters) {
  const segments = Object.fromEntries(b.segments.map(s => [s.id, s]));
  const total = b.schedule.reduce((sum, e) => sum + e.duration, 0);
  const voices = [...new Set(b.segments.map(s => presenters[s.presenter]?.name).filter(Boolean))].join(", ");
  if (!b.audio_available) {
    return `<section class="panel bulletin" id="${esc(b.id)}">
      <h2>${esc(title(b))}</h2>
      <p class="meta">${clock(b.starts_at, 0).slice(0, 5)} · text only: ${esc(b.tts_note || "no voice for this language yet")}</p>
      ${b.segments.filter(s => s.script).map(s => `<div class="seg"><div class="meta"><strong class="ink">${esc(KIND[s.kind] || s.kind)}</strong></div>
        <p class="script">${esc(s.script)}</p></div>`).join("")}
    </section>`;
  }
  return `<section class="panel bulletin" id="${esc(b.id)}">
    <h2>${esc(title(b))}</h2>
    <p class="meta">Aired at ${clock(b.starts_at, 0).slice(0, 5)} · ${minutes(total)} · ${esc(voices)}</p>
    <button class="primary" data-play="${esc(b.id)}">Play from the top</button>
    <ol class="segments">${b.schedule.map(e => segmentRow(b, e, segments[e.segment_id], presenters)).join("")}</ol>
  </section>`;
}

// Plays a bulletin's segments one after another (time checks are left out, as in replays on air).
function playFromTop(card) {
  document.querySelectorAll("audio").forEach(a => a.pause());
  const players = [...card.querySelectorAll("li.seg")].filter(li => li.dataset.kind !== "timecheck").map(li => li.querySelector("audio"));
  const play = i => {
    if (i >= players.length) return;
    players[i].currentTime = 0;
    players[i].onended = () => play(i + 1);
    players[i].scrollIntoView({ block: "nearest", behavior: "smooth" });
    players[i].play().catch(() => {});
  };
  play(0);
}

async function render() {
  const [list, station] = await Promise.all([api("/bulletins"), api("/station").catch(() => ({ presenters: {} }))]);
  const lang = params.get("lang");
  const chosen = list.filter(b => !lang || b.language === lang)
    .sort((a, b) => a.starts_at.localeCompare(b.starts_at) || ORDER.indexOf(a.language) - ORDER.indexOf(b.language));
  const bulletins = await Promise.all(chosen.map(b => api(`/bulletins/${encodeURIComponent(b.id)}`)));
  main.innerHTML = `
    <h1>Every bulletin, on demand</h1>
    <p class="lede">The Listen page follows the station clock, like a radio. Here you can play any of the day's bulletins
      from the top, or any single segment, and read the script along with it. The voices are synthetic.</p>
    <p class="meta">Language:
      <a href="bulletins.html"${!lang ? ' class="ink"' : ""}>all</a>
      ${ORDER.map(code => ` · <a href="?lang=${code}"${lang === code ? ' class="ink"' : ""}>${LANGUAGES[code]}</a>`).join("")}</p>
    ${bulletins.map(b => bulletinCard(b, station.presenters || {})).join("") || "<p class='muted'>No bulletins yet.</p>"}`;
  main.querySelectorAll("[data-play]").forEach(button =>
    button.addEventListener("click", () => playFromTop(button.closest(".bulletin"))));
  if (location.hash) document.getElementById(decodeURIComponent(location.hash.slice(1)))?.scrollIntoView();
}

render().catch(err => { main.innerHTML = `<p class="error">${esc(err.message)}</p>`; });
