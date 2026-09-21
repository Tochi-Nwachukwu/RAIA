// The rejection log: stories and scripts the station refused to air, and why.
import { api, chrome, esc } from "./app.js";

chrome("rejections.html");
const main = document.getElementById("main");
const STAGE = { editor: "Editor", gate_rules: "Safety gate (rules)", gate_review: "Safety gate (review)",
  human_review: "Human review", tease_check: "Continuity check" };

api("/sources/rejections").then(data => {
  const rows = data.rejections.sort((a, b) => a.rule.localeCompare(b.rule));
  main.innerHTML = `
    <h1>${data.stories_rejected} stories we refused to air</h1>
    <p class="lede">A station that has thought about when not to broadcast is a serious one. Sensitive stories wait for a
      human editor - there is no override switch. Stories we could not verify do not air. Every script passes a final
      safety gate. This is the full list for ${esc(data.date)}.</p>
    <section class="panel"><table>
      <tr><th>Story</th><th>Refused by</th><th>Rule</th><th>Why</th></tr>
      ${rows.map(r => `<tr><td>${esc(r.headline || r.segment_id || "")}</td><td>${esc(STAGE[r.stage] || r.stage)}</td>
        <td>${esc(r.rule)}</td><td class="src">${esc(r.reason)}</td></tr>`).join("")}
    </table></section>`;
}).catch(err => { main.innerHTML = `<p class="error">${esc(err.message)}</p>`; });
