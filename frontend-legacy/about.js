// About: the disclosure, and the models the station runs on.
import { api, chrome, esc } from "./app.js";

chrome("about.html");
const main = document.getElementById("main");

api("/station").then(s => {
  const tts = Object.entries(s.tts).map(([lang, t]) => `<tr><td>${esc(s.languages[lang] || lang)}</td>
    <td>${t.engine === "yarngpt" ? esc(t.voice_model) : "No voice yet"}</td><td class="src">${esc(t.note || "")}</td></tr>`).join("");
  const used = Object.entries(s.models_used?.calls || {}).sort((a, b) => b[1].calls - a[1].calls)
    .map(([model, u]) => `${esc(model)} (${u.calls})`);
  const presenters = Object.values(s.presenters).map(p => `${esc(p.name)} (${p.languages.map(esc).join(", ")})`).join(", ");
  main.innerHTML = `
    <h1>About RAIA</h1>
    <p class="lede">RAIA - Radio AI Africa - is a civic radio station produced by AI agents and delivered as a scheduled
      broadcast. Agents gather news and sport from named sources, check claims against each other, write spoken copy,
      translate it, and render it to audio ahead of time.</p>
    <section class="panel">
      <h2 style="margin-top:0">The presenters are synthetic</h2>
      <p>${esc(s.disclosure)}</p>
      <p>We say so on air once an hour, in every language, and it cannot be turned off. Our presenters: ${presenters}.</p>
    </section>
    <h2>The models we use</h2>
    <section class="panel"><table>
      <tr><th>Job</th><th>Model</th></tr>
      <tr><td>High-volume extraction</td><td>${s.models.cheap.map(esc).join(", ")}</td></tr>
      <tr><td>Scripts, continuity, listener desk</td><td>${s.models.mid.map(esc).join(", ")}</td></tr>
      <tr><td>Verification, editing, translation, safety review</td><td>${s.models.expensive.map(esc).join(", or when it is unavailable ")}</td></tr>
      <tr><td>Grouping reports of the same event</td><td>${esc(s.embeddings)}</td></tr>
    </table>
    <p class="src">Every language-model call goes through one gateway in the code; the provider (now: ${esc(s.llm_provider)}) and
      the model for each job are configuration.</p>
    ${used.length ? `<p class="src">Model calls recorded for ${esc(s.models_used.date)}: ${used.join(", ")}.</p>` : ""}</section>
    <h2>Voices</h2>
    <section class="panel"><table><tr><th>Language</th><th>Voice model</th><th></th></tr>${tts}</table></section>`;
}).catch(err => { main.innerHTML = `<p class="error">${esc(err.message)}</p>`; });
