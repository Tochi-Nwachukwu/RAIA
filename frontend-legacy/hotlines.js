// Hotlines: only numbers confirmed on an official page, with the date they were checked.
import { api, chrome, esc } from "./app.js";

chrome("hotlines.html");
const main = document.getElementById("main");

api("/hotlines").then(data => {
  main.innerHTML = `
    <h1>Numbers to keep close</h1>
    <p class="lede">We never invent a number. Each one below was found on the agency's own website (or, where that site
      blocks checks, a federal broadcaster quoting the agency) on the date shown.</p>
    <section class="panel"><table>
      <tr><th>Service</th><th>Number</th><th>For</th><th>Checked</th></tr>
      ${data.on_air.map(h => `<tr><td>${esc(h.service)}</td><td><strong>${h.numbers.map(esc).join("<br>")}</strong></td>
        <td>${esc(h.purpose)}${h.hours ? `<br><span class="src">${esc(h.hours)}</span>` : ""}${h.note ? `<br><span class="src">${esc(h.note)}</span>` : ""}</td>
        <td><a href="${esc(h.source_url)}" target="_blank" rel="noopener">${esc(h.verified_on)}</a></td></tr>`).join("")}
    </table>
    <p class="src">Not read on air until confirmed: ${data.not_aired.map(n => `${esc(n.service)} (${esc(n.reason)})`).join("; ")}.</p>
    </section>`;
}).catch(err => { main.innerHTML = `<p class="error">${esc(err.message)}</p>`; });
