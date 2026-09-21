// Shared by every page: the API location, the permanent disclosure, and navigation.
// The API defaults to http://localhost:8000; override with ?api=https://your-host.
const params = new URLSearchParams(location.search);
if (params.get("api")) localStorage.setItem("raia.api", params.get("api"));
export const API = (localStorage.getItem("raia.api") || window.RAIA_API || "http://localhost:8000").replace(/\/$/, "");

export async function api(path) {
  const response = await fetch(API + path);
  if (!response.ok) {
    const body = await response.json().catch(() => ({}));
    throw new Error(body.detail || `${response.status} ${response.statusText}`);
  }
  return response.json();
}

export const LANGUAGES = { en: "English", pcm: "Pidgin", ha: "Hausa", yo: "Yoruba", ig: "Igbo" };
const DISCLOSURE = "RAIA's presenters are synthetic voices made by artificial intelligence. Every story names its sources - check them on the Sources page.";

export function chrome(active) {
  // The disclosure is on every page and cannot be dismissed.
  document.body.insertAdjacentHTML("afterbegin", `
    <div class="disclosure" role="note">${DISCLOSURE}</div>
    <header class="site">
      <a class="brand" href="index.html">RAIA <span>Radio AI Africa</span></a>
      <nav class="site">
        ${[["index.html", "Listen"], ["bulletins.html", "Bulletins"], ["sources.html", "Sources"], ["rejections.html", "What we refused"],
           ["hotlines.html", "Hotlines"], ["about.html", "About"]]
          .map(([href, label]) => `<a href="${href}" class="${href === active ? "active" : ""}">${label}</a>`).join("")}
      </nav>
    </header>`);
  document.body.insertAdjacentHTML("beforeend", `
    <footer class="site">RAIA is a civic radio station produced by AI agents. ${DISCLOSURE}</footer>`);
}

export function esc(text) {
  return String(text ?? "").replace(/[&<>"']/g, c => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" })[c]);
}

export function when(iso) {
  return new Date(iso).toLocaleString(undefined, { dateStyle: "medium", timeStyle: "short" });
}

export const STATUS = {
  corroborated: ["good", "corroborated"], single_source: ["warn", "one outlet only"],
  attributed: ["warn", "attributed claim"], disputed: ["bad", "disputed - not aired"],
  verified: ["good", "verified"], developing: ["warn", "developing"], unverified: ["bad", "unverified"],
};

export function chip(key) {
  const [tone, label] = STATUS[key] || ["", key];
  return `<span class="chip ${tone}">${esc(label)}</span>`;
}
