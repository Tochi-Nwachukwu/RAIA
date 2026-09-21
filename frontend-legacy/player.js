// The wall-clock player. It asks the server what time it is and what is on air, loads that file,
// seeks to the offset and plays; at each segment boundary (and every 15 seconds) it re-syncs, so
// listeners in different cities hear the same sentence at the same moment.
//
// ?at=2026-09-19T06:05:30+01:00 pretends the page opened at that time (the S5 check: land mid-segment).
import { api, chrome, chip, esc, LANGUAGES } from "./app.js";

chrome("index.html");

const $ = id => document.getElementById(id);
const audio = $("audio"), bed = $("bed");
const params = new URLSearchParams(location.search);
const simulatedStart = params.get("at") ? Date.parse(params.get("at")) : null;
const pageOpened = Date.now();
const KIND = { station_id: "Station ID", disclosure: "Disclosure", headlines: "Headlines", story: "Story",
  explainer: "Explainer", sport: "Sport", listener: "Your questions", hotlines: "Hotlines", handover: "Handover",
  tease: "Coming up", timecheck: "Time check", bed: "Music bed" };

let presenters = {}, playing = false, current = null, received = 0, resyncTimer = null;

const lang = $("lang");
lang.innerHTML = Object.entries(LANGUAGES).map(([code, name]) => `<option value="${code}">${name}</option>`).join("");
lang.value = params.get("lang") || localStorage.getItem("raia.lang") || "en";
lang.onchange = () => { localStorage.setItem("raia.lang", lang.value); if (playing) tuneIn(); else preview(); };

// The listener's clock, or the simulated one.
const clockNow = () => simulatedStart ? simulatedStart + (Date.now() - pageOpened) : Date.now();

async function fetchNow() {
  const at = simulatedStart ? `&at=${encodeURIComponent(new Date(clockNow()).toISOString())}` : "";
  const sent = Date.now();
  const now = await api(`/now?lang=${lang.value}${at}`);
  received = Date.now();
  const drift = Date.parse(now.server_time) - (simulatedStart ? clockNow() - (received - sent) / 2 : (sent + received) / 2);
  $("sync").textContent = simulatedStart ? `Simulated time ${new Date(clockNow()).toLocaleTimeString()}`
    : `In sync with the station clock (${Math.abs(drift) < 1000 ? "under a second" : Math.round(drift / 1000) + "s"} apart)`;
  return now;
}

// Where the current entry should be, right now.
const expectedPosition = () => current.position + (Date.now() - received) / 1000;

function show(now) {
  $("error").hidden = true;
  const live = now.mode === "live";
  $("onair").className = "onair" + (live ? " live" : "");
  $("mode").textContent = { live: "On air", loop: "Replay of the latest bulletin", text: "Text only" }[now.mode] || now.mode;
  $("rejected").textContent = now.bulletin.stories_rejected;
  $("sourcecount").textContent = now.bulletin.source_count;
  if (now.mode === "text") {
    $("title").textContent = `${LANGUAGES[now.language]} bulletin (text)`;
    $("meta").textContent = now.bulletin.tts_note || "No voice is available for this language yet.";
    $("caption").hidden = false;
    $("caption").textContent = now.segments.map(s => s.script).join("\n\n");
    $("upnext").innerHTML = `<li class="muted">Audio for ${LANGUAGES[now.language]} is coming: its script is shown instead.</li>`;
    return;
  }
  const entry = now.current;
  const who = presenters[entry.presenter]?.name || entry.presenter || "";
  $("title").textContent = KIND[entry.kind] || entry.kind;
  $("meta").innerHTML = [esc(who), esc(now.bulletin.block.replaceAll("_", " ")),
    entry.sources_count ? `${entry.sources_count} source${entry.sources_count > 1 ? "s" : ""} ${chip(entry.confidence)}
      <a href="sources.html#${esc(entry.story_id)}">check them</a>` : ""].filter(Boolean).join(" · ");
  $("caption").hidden = !entry.script;
  $("caption").textContent = entry.script || "";
  $("upnext").innerHTML = now.next.map(e => `<li>${esc(KIND[e.kind] || e.kind)}${e.script ? ` - <span class="muted">${esc(e.script.split(/(?<=[.!?])\s/)[0].slice(0, 90))}</span>` : ""}</li>`).join("");
}

async function load(entry, position) {
  audio.src = entry.audio_url;
  await new Promise((resolve, reject) => {
    audio.onloadedmetadata = resolve;
    audio.onerror = () => reject(new Error("Could not load the audio file"));
  });
  audio.currentTime = Math.min(Math.max(position, 0), Math.max(audio.duration - 0.05, 0));
  if (playing) await audio.play();
}

async function tuneIn() {
  try {
    playing = true;
    $("play").disabled = true;
    const now = await fetchNow();
    show(now);
    if (now.mode === "text") { playing = false; $("play").disabled = false; return; }
    current = { ...now.current, bulletin: now.bulletin.id };
    await load(now.current, expectedPosition());
    if (now.mode === "loop" && now.bed_url) { bed.src = now.bed_url; bed.volume = 0.12; bed.play().catch(() => {}); }
    else bed.pause();
    $("play").hidden = true; $("stop").hidden = false;
    clearInterval(resyncTimer);
    resyncTimer = setInterval(resync, 15000);
  } catch (err) {
    fail(err);
  }
}

async function resync() {
  if (!playing) return;
  try {
    const now = await fetchNow();
    if (now.mode === "text") return show(now);
    if (now.current.segment_id !== current.segment_id || now.bulletin.id !== current.bulletin) {
      show(now);
      current = { ...now.current, bulletin: now.bulletin.id };
      return load(now.current, expectedPosition());
    }
    current = { ...now.current, bulletin: now.bulletin.id };
    if (audio.ended) return setTimeout(resync, 250);  // the server clock has not crossed into the next segment yet
    const drift = audio.currentTime - expectedPosition();
    if (Math.abs(drift) > 0.75) audio.currentTime = expectedPosition();
  } catch (err) {
    fail(err);
  }
}

audio.addEventListener("ended", resync);
audio.addEventListener("timeupdate", () => {
  if (audio.duration) $("bar").style.width = `${(100 * audio.currentTime) / audio.duration}%`;
});

function stop() {
  playing = false;
  audio.pause(); bed.pause();
  clearInterval(resyncTimer);
  $("play").hidden = false; $("play").disabled = false; $("stop").hidden = true;
}

function fail(err) {
  $("error").hidden = false;
  $("error").textContent = err.message.includes("Failed to fetch")
    ? "Cannot reach the RAIA API. Is it running on " + (localStorage.getItem("raia.api") || "http://localhost:8000") + "?"
    : err.message;
  stop();
}

// Show what is on air before the listener presses play (browsers need a click before audio).
async function preview() {
  try { show(await fetchNow()); } catch (err) { $("title").textContent = "Nothing on air yet in this language"; $("meta").textContent = err.message; }
}

$("play").onclick = tuneIn;
$("stop").onclick = stop;
api("/station").then(s => { presenters = s.presenters; }).catch(() => {}).finally(preview);
