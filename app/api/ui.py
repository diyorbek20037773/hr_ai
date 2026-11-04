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
  .ghost { background:transparent; color:var(--accent); border:1px solid var(--accent); }
  .danger { background:transparent; color:var(--bad); border:1px solid var(--bad); }
  .badge { display:inline-block; padding:2px 8px; border-radius:6px; font-size:12px; font-weight:600;
           background:var(--okbg); color:var(--ok); }
  .badge.custom { background:var(--warnbg); color:var(--warn); }
  .mono { font-family: ui-monospace, "Cascadia Code", Consolas, monospace; font-size:13px; }
  textarea.mono { min-height: 320px; }
  .shot { border:1px solid var(--line); border-radius:8px; padding:10px; margin-top:8px; }
  .shot input { width:100%; padding:7px 9px; font:inherit; color:inherit; background:var(--bg);
                border:1px solid var(--line); border-radius:6px; }
  .shot textarea { min-height:44px; margin-top:6px; }
  .shot .row { margin-top:6px; justify-content:space-between; }
  .note { font-size:13px; padding:8px 10px; border-radius:8px; background:var(--warnbg); color:var(--warn); }
  .err { font-size:13px; padding:8px 10px; border-radius:8px; background:var(--badbg); color:var(--bad); }
  .okmsg { font-size:13px; padding:8px 10px; border-radius:8px; background:var(--okbg); color:var(--ok); }
  .head { display:flex; gap:10px; align-items:center; justify-content:space-between; flex-wrap:wrap; }
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
      <button id="promptBtn" class="ghost">Prompt (Qwen'ga beriladigan)</button>
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

  <div class="card" id="promptPanel" hidden>
    <div class="head">
      <div><strong>Qwen'ga beriladigan prompt</strong> <span id="pVersion" class="badge"></span>
        <span id="pTokens" class="muted"></span></div>
      <button id="pClose" class="ex">Yopish</button>
    </div>
    <p class="muted" style="margin:8px 0 0">Har bir so'rovda modelga ketma-ket yuboriladi: <b>system</b> xabar →
      har bir misol uchun <b>user</b> (so'rov) + <b>assistant</b> (JSON javob) → oxirida sizning so'rovingiz.</p>
    <div id="pMsg" style="margin-top:10px"></div>

    <h2>System prompt</h2>
    <textarea id="pSystem" class="mono" spellcheck="false"></textarea>

    <div class="head" style="margin-top:12px">
      <h2 style="margin:0">Few-shot misollar (<span id="pShotCount">0</span>)</h2>
      <button id="pAdd" class="ex">+ Misol qo'shish</button>
    </div>
    <div id="pShots"></div>
    <p class="muted">Javob JSON bo'lishi shart: faqat intent kalitlari (region, sex, fromAge, …), ID yozilmaydi.</p>

    <div class="row">
      <button id="pSave">Saqlash va qo'llash</button>
      <button id="pReset" class="danger">Standartga qaytarish</button>
      <button id="pRender" class="ghost">Aniq matnni ko'rish (joriy so'rov bilan)</button>
    </div>
    <p class="note" style="margin-top:10px">Tahrir faqat ishlab turgan server xotirasiga qo'llanadi va
      <b>eval qilinmagan</b> hisoblanadi (versiya “+custom”). Server qayta ishga tushsa standart prompt qaytadi.
      Doimiy qilish uchun <code>app/prompts/search_parser.py</code> ga ko'chirib, eval'ni ishga tushiring.</p>
    <div id="pRenderedBox" hidden>
      <h2>Modelga kiradigan aniq matn <span id="pRenderedInfo" class="muted"></span></h2>
      <pre id="pRendered" class="mono" style="max-height:420px"></pre>
    </div>
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
      ? `· model tayyor (${h.device}, ${h.dtype}, prompt v${h.promptVersion}` +
        (h.promptCustom ? " — TAHRIRLANGAN" : "") + ")"
      : `· model yuklanmoqda (${h.modelStatus})…`;
    if (!h.modelLoaded) setTimeout(health, 3000);
  } catch { $("status").textContent = "· server javob bermayapti"; }
}

async function run() {
  const query = $("q").value.trim();
  if (!query) return;
  $("go").disabled = true; $("go").textContent = "Kutilmoqda…";
  $("out").hidden = false;
  $("meta").textContent = "Model javob yozmoqda… odatda 3–10 soniya, CPU'da bir necha daqiqa.";
  for (const id of ["chips", "unres", "warns"]) $(id).innerHTML = "";
  $("sm").textContent = ""; $("raw").textContent = "";
  $("out").scrollIntoView({behavior: "smooth", block: "nearest"});
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
    $("meta").textContent = "";
    $("chips").innerHTML = `<span class="chip bad">Server bilan aloqa yo'q: ${esc(e.message)}</span>`;
  } finally {
    $("go").disabled = false; $("go").textContent = "Qidirish";
    $("out").scrollIntoView({behavior: "smooth", block: "nearest"});
  }
}
$("go").onclick = run;
$("q").addEventListener("keydown", (e) => { if (e.key === "Enter" && e.ctrlKey) run(); });
// ---------------- prompt viewer / editor ----------------
function shotEl(query = "", answer = {}) {
  const d = document.createElement("div");
  d.className = "shot";
  d.innerHTML = `<input class="sq" placeholder="Foydalanuvchi so'rovi" maxlength="1000">
    <textarea class="sa mono" spellcheck="false" placeholder='{"sex": "Ayol", "fromAge": 30}'></textarea>
    <div class="row"><span class="muted sn"></span><button class="ex del">O'chirish</button></div>`;
  d.querySelector(".sq").value = query;
  d.querySelector(".sa").value = JSON.stringify(answer, null, 0);
  d.querySelector(".del").onclick = () => { d.remove(); renumber(); };
  return d;
}
function renumber() {
  const shots = [...$("pShots").children];
  shots.forEach((el, i) => el.querySelector(".sn").textContent = `#${i + 1}`);
  $("pShotCount").textContent = shots.length;
}
function msg(kind, html) { $("pMsg").innerHTML = html ? `<div class="${kind}">${html}</div>` : ""; }
function showState(st) {
  $("pVersion").textContent = st.isCustom ? `v${st.version} (tahrirlangan)` : `v${st.version} (standart)`;
  $("pVersion").className = "badge" + (st.isCustom ? " custom" : "");
  $("pTokens").textContent = st.tokenCount
    ? `· ${st.tokenCount} / ${st.tokenLimit} token (namuna so'rov bilan)` : "· token soni: model yuklanmagan";
  $("pSystem").value = st.systemPrompt;
  $("pShots").innerHTML = "";
  for (const s of st.fewShots) $("pShots").appendChild(shotEl(s.query, s.answer));
  renumber();
  for (const b of ["pSave", "pReset", "pAdd"]) $(b).disabled = !st.editingEnabled;
  if (!st.editingEnabled) msg("note", "Tahrirlash o'chirilgan (PROMPT_EDITING_ENABLED=false) — faqat ko'rish mumkin.");
  health();
}
async function api(method, path, body) {
  const res = await fetch(path, { method, headers: {"Content-Type": "application/json"},
                                  body: body ? JSON.stringify(body) : undefined });
  const data = await res.json();
  if (!res.ok) throw new Error(data.error ? `${data.error.code}: ${data.error.message}` : res.statusText);
  return data;
}
async function openPrompt() {
  $("promptPanel").hidden = false; msg("", "");
  try { showState(await api("GET", "/api/v1/prompt")); }
  catch (e) { msg("err", esc(e.message)); }
  $("promptPanel").scrollIntoView({behavior: "smooth", block: "start"});
}
async function savePrompt() {
  const fewShots = [];
  const shots = [...$("pShots").children];
  for (let i = 0; i < shots.length; i++) {
    const query = shots[i].querySelector(".sq").value.trim();
    const raw = shots[i].querySelector(".sa").value.trim() || "{}";
    let answer;
    try { answer = JSON.parse(raw); } catch { return msg("err", `Misol #${i + 1}: javob JSON emas.`); }
    if (!query) return msg("err", `Misol #${i + 1}: so'rov bo'sh.`);
    fewShots.push({query, answer});
  }
  $("pSave").disabled = true;
  try {
    const st = await api("PUT", "/api/v1/prompt", {systemPrompt: $("pSystem").value, fewShots});
    showState(st);
    msg("okmsg", "Saqlandi. Keyingi so'rovlar shu prompt bilan ishlaydi." +
        (st.warnings.length ? "<br>" + st.warnings.map(esc).join("<br>") : ""));
  } catch (e) { msg("err", esc(e.message)); }
  finally { $("pSave").disabled = false; }
}
async function resetPrompt() {
  if (!confirm("Standart promptga qaytarilsinmi? Tahrirlaringiz o'chadi.")) return;
  try { showState(await api("DELETE", "/api/v1/prompt")); msg("okmsg", "Standart prompt tiklandi."); }
  catch (e) { msg("err", esc(e.message)); }
}
async function renderPrompt() {
  try {
    const r = await api("POST", "/api/v1/prompt/render", {query: $("q").value.trim() || "test"});
    $("pRenderedBox").hidden = false;
    $("pRendered").textContent = r.rendered;
    $("pRenderedInfo").textContent = r.exact
      ? `· Qwen chat-template qo'llangan, ${r.tokenCount} token · v${r.version}`
      : "· model yuklanmagan: taxminiy ko'rinish (chat-template'siz)";
    $("pRenderedBox").scrollIntoView({behavior: "smooth", block: "nearest"});
  } catch (e) { msg("err", esc(e.message)); }
}
$("promptBtn").onclick = openPrompt;
$("pClose").onclick = () => { $("promptPanel").hidden = true; };
$("pAdd").onclick = () => { $("pShots").appendChild(shotEl("", {})); renumber(); };
$("pSave").onclick = savePrompt;
$("pReset").onclick = resetPrompt;
$("pRender").onclick = renderPrompt;
health();
</script>
</body>
</html>
"""


@router.get("/", response_class=HTMLResponse)
async def index() -> HTMLResponse:
    return HTMLResponse(_PAGE)
