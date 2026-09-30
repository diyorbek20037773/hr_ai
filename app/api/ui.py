"""Minimal self-contained test page at "/" (no CDN, works offline). Not part of the frontend contract."""

from __future__ import annotations

from fastapi import APIRouter
from fastapi.responses import HTMLResponse

router = APIRouter(include_in_schema=False)

_PAGE = """<!doctype html>
<html lang="uz">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>hr_ai — AI Smart Search</title>
<style>
  :root { --bg:#f6f7f9; --card:#fff; --text:#1d2330; --muted:#667085; --line:#dde1e7; --accent:#2f5bd3;
          --ok:#0f7a4a; --okbg:#e6f4ec; --warn:#9a5b00; --warnbg:#fff4e0; --bad:#b42318; --badbg:#fdecea; }
  @media (prefers-color-scheme: dark) {
    :root { --bg:#14171c; --card:#1c2027; --text:#e6e8ec; --muted:#9aa3b2; --line:#2e333d; --accent:#7ea2ff;
            --ok:#6fd3a0; --okbg:#173327; --warn:#f0b35a; --warnbg:#3a2c14; --bad:#ff8a80; --badbg:#3a1c1a; }
  }
  * { box-sizing: border-box; }
  body { margin:0; background:var(--bg); color:var(--text); font:15px/1.5 system-ui, "Segoe UI", sans-serif; }
  main { max-width: 860px; margin: 0 auto; padding: 24px 16px 48px; }
  h1 { font-size: 20px; margin: 0 0 4px; }
  .sub { color: var(--muted); margin: 0 0 16px; }
  .card { background:var(--card); border:1px solid var(--line); border-radius:10px; padding:16px; margin-top:14px; }
  textarea { width:100%; min-height:72px; padding:10px; font:inherit; color:inherit; background:var(--bg);
             border:1px solid var(--line); border-radius:8px; resize:vertical; }
  .row { display:flex; gap:8px; flex-wrap:wrap; align-items:center; margin-top:10px; }
  button { background:var(--accent); color:#fff; border:0; border-radius:8px; padding:9px 16px; font:inherit;
           cursor:pointer; }
  button:disabled { opacity:.6; cursor:wait; }
  .ex { background:transparent; color:var(--accent); border:1px solid var(--line); padding:5px 10px; font-size:13px; }
  .chip { display:inline-block; padding:4px 10px; border-radius:999px; margin:3px 4px 3px 0; font-size:13px;
          background:var(--okbg); color:var(--ok); }
  .chip.warn { background:var(--warnbg); color:var(--warn); }
  .chip.bad { background:var(--badbg); color:var(--bad); }
  .muted { color:var(--muted); font-size:13px; }
  pre { background:var(--bg); border:1px solid var(--line); border-radius:8px; padding:10px; overflow:auto;
        font-size:13px; margin:6px 0 0; }
  h2 { font-size:14px; margin:12px 0 4px; color:var(--muted); font-weight:600; }
  a { color:var(--accent); }
  #status { font-size:13px; }
</style>
</head>
<body>
<main>
  <h1>AI Smart Search — test sahifasi</h1>
  <p class="sub">Bir gap yozing — lokal Qwen modeli filtrlarni aniqlaydi va <code>ISearchModel</code> quradi.
    <span id="status" class="muted"></span></p>

  <div class="card">
    <textarea id="q" maxlength="1000">Toshkentda yashaydigan, 30 yoshdan katta, oliy ma'lumotli ayollar</textarea>
    <div class="row">
      <button id="go">Qidirish</button>
      <span class="muted">Ctrl+Enter</span>
    </div>
    <div class="row" id="examples"></div>
  </div>

  <div class="card" id="out" hidden>
    <div class="muted" id="meta"></div>
    <h2>Aniqlangan filtrlar</h2><div id="chips"></div>
    <div id="unres"></div>
    <div id="warns"></div>
    <h2>searchModel (mavjud HR Search API'ga yuboriladi)</h2><pre id="sm"></pre>
    <details><summary class="muted">To'liq javob (JSON)</summary><pre id="raw"></pre></details>
  </div>

  <p class="muted" style="margin-top:18px">
    <a href="/docs">Swagger /docs</a> · <a href="/health">/health</a> · <a href="/ready">/ready</a> ·
    <a href="/metrics">/metrics</a> · <a href="/api/v1/references">/api/v1/references</a>
  </p>
</main>
<script>
const EXAMPLES = [
  "Samarqand viloyatidagi 25-40 yoshli davlat xizmatchilari",
  "Олий маълумотли 30 ёшдан катта аёллар",
  "Женщины старше 30 лет с высшим образованием",
  "Chilonzor tumanida yashaydigan, ingliz tilini biladigan, IELTS sertifikati bor yoshlar 35 yoshgacha",
  "bo'lim boshliqlari va direktorlar",
  "yaxshi kadrlarni top",
];
const $ = (id) => document.getElementById(id);
const esc = (s) => String(s).replace(/[&<>"]/g, (c) => ({"&":"&amp;","<":"&lt;",">":"&gt;",'"':"&quot;"}[c]));

for (const e of EXAMPLES) {
  const b = document.createElement("button");
  b.className = "ex"; b.textContent = e.length > 42 ? e.slice(0, 40) + "…" : e; b.title = e;
  b.onclick = () => { $("q").value = e; run(); };
  $("examples").appendChild(b);
}

async function health() {
  try {
    const h = await (await fetch("/health")).json();
    $("status").textContent = h.modelLoaded
      ? `· model tayyor (${h.device}, ${h.dtype}, prompt v${h.promptVersion})`
      : `· model yuklanmoqda (${h.modelStatus})…`;
    if (!h.modelLoaded) setTimeout(health, 3000);
  } catch { $("status").textContent = "· server javob bermayapti"; }
}

async function run() {
  const query = $("q").value.trim();
  if (!query) return;
  $("go").disabled = true; $("go").textContent = "Kutilmoqda…";
  const t0 = performance.now();
  try {
    const res = await fetch("/api/v1/ai-search/resolve", {
      method: "POST", headers: {"Content-Type": "application/json"}, body: JSON.stringify({query}),
    });
    const data = await res.json();
    $("out").hidden = false;
    $("raw").textContent = JSON.stringify(data, null, 2);
    if (!res.ok) {
      $("meta").textContent = "";
      $("chips").innerHTML = `<span class="chip bad">${esc(data.error?.code)}: ${esc(data.error?.message)}</span>`;
      $("unres").innerHTML = ""; $("warns").innerHTML = ""; $("sm").textContent = "";
      return;
    }
    $("meta").textContent = `${data.latencyMs} ms (model) · ${Math.round(performance.now() - t0)} ms (jami) · ` +
      `readyToSearch: ${data.readyToSearch}` +
      (data.corrections?.length ? ` · tuzatishlar: ${data.corrections.join(", ")}` : "");
    $("chips").innerHTML = data.recognizedFilters.length
      ? data.recognizedFilters.map((f) => `<span class="chip" title="${esc(f.labelRu)} · ${esc(f.matchType)}">` +
          `${esc(f.label)}: ${esc(f.displayValue)}</span>`).join("")
      : '<span class="muted">Filtr aniqlanmadi</span>';
    $("unres").innerHTML = data.unresolvedFilters.length
      ? "<h2>Aniqlanmagan (tanlash kerak)</h2>" + data.unresolvedFilters.map((u) =>
          `<span class="chip warn">${esc(u.label)}: “${esc(u.inputValue)}” — ${esc(u.reason)}` +
          (u.candidates.length ? " → " + u.candidates.map((c) => `${esc(c.name)} (${c.score})`).join(", ") : "") +
          "</span>").join("")
      : "";
    $("warns").innerHTML = data.warnings.length
      ? "<h2>Ogohlantirishlar</h2>" + data.warnings.map((w) => `<div class="muted">• ${esc(w)}</div>`).join("") : "";
    $("sm").textContent = JSON.stringify(data.searchModel, null, 2);
  } catch (e) {
    $("out").hidden = false;
    $("chips").innerHTML = `<span class="chip bad">Server bilan aloqa yo'q: ${esc(e.message)}</span>`;
  } finally {
    $("go").disabled = false; $("go").textContent = "Qidirish";
  }
}
$("go").onclick = run;
$("q").addEventListener("keydown", (e) => { if (e.key === "Enter" && e.ctrlKey) run(); });
health();
</script>
</body>
</html>
"""


@router.get("/", response_class=HTMLResponse)
async def index() -> HTMLResponse:
    return HTMLResponse(_PAGE)
