// Studio Utility web app: one shared camera, live state from the hub's event stream.
const $ = (id) => document.getElementById(id);
const DEVICE = /iPhone/.test(navigator.userAgent) ? "iPhone" : /iPad/.test(navigator.userAgent) ? "iPad"
  : /Android/.test(navigator.userAgent) ? "Android phone" : /Mac/.test(navigator.userAgent) ? "Mac"
  : /Windows/.test(navigator.userAgent) ? "Windows PC" : "a device";
let state = { settings: {}, status: {}, effective: {}, caps: [] };
let seq = 0;

async function api(path, body, method) {
  const r = await fetch("/api/" + path, {
    method: method || (body !== undefined ? "POST" : "GET"),
    headers: { "Content-Type": "application/json", "X-Studio-Device": DEVICE },
    body: body !== undefined ? JSON.stringify(body) : undefined,
  });
  if (!r.ok) throw new Error((await r.text()) || r.statusText);
  return r.headers.get("content-type")?.includes("json") ? r.json() : r;
}

let toastTimer;
function toast(msg) {
  const t = $("toast");
  t.textContent = msg;
  t.classList.add("show");
  clearTimeout(toastTimer);
  toastTimer = setTimeout(() => t.classList.remove("show"), 2600);
}
const haptic = () => navigator.vibrate && navigator.vibrate(8);

// ---------- rendering
function render() {
  const connected = state.connection === "connected";
  $("dot").className = "dot " + (connected ? "on" : String(state.connection || "").startsWith("lost") ? "off" : "");
  $("model").textContent = state.info?.model || "Studio Utility";
  $("conn").textContent = connected ? `Connected · ${(state.info?.firmware && "fw " + state.info.firmware) || ""}` : state.connection || "Connecting…";
  const s = state.status || {};
  const bat = typeof s.battery === "number" ? s.battery : null;  // "" = USB power / charging
  setPill("bat", bat != null ? `${bat}%` : s.battery === "" ? "🔌" : null, bat != null && bat <= 15);
  setPill("card", s.card_free_gb != null ? `${s.card_free_gb} GB${s.shots_left ? " · " + s.shots_left : ""}` : null);
  const e = state.effective || {};
  setPill("exp", e.tv || e.av || e.iso ? [e.tv && label("tv", e.tv), e.av && label("av", e.av), e.iso && "ISO " + e.iso].filter(Boolean).join("  ") : null);
  const rec = state.recording;
  $("rec").classList.toggle("hidden", !rec);
  $("recbtn").classList.toggle("on", !!rec);
  for (const id of ["recbtn", "shoot", "af"]) $(id).disabled = !connected;
  // power zoom only over Wi-Fi; USB has zoom speed but no zoom drive
  document.querySelector(".zoom").classList.toggle("hidden", connected && !(state.caps || []).includes("zoom_move"));
  renderSettings();
  renderQuick();
}
function setPill(id, text, warn) {
  const p = $(id);
  p.classList.toggle("hidden", !text);
  p.classList.toggle("warn", !!warn);
  if (text) p.textContent = text;
}
setInterval(() => {
  const rec = state.recording;
  if (!rec) return;
  const secs = Math.max(0, Math.floor(Date.now() / 1000 - rec.since + (state.clockSkew || 0)));
  $("rectime").textContent = `${String(Math.floor(secs / 60)).padStart(2, "0")}:${String(secs % 60).padStart(2, "0")}`;
}, 250);

function renderSettings() {
  const list = $("settings");
  const keys = Object.keys(SETTING_NAMES).filter((k) => state.settings?.[k]);
  list.innerHTML = keys.length ? "" : `<div class="row"><span class="sub">Settings appear when the camera is connected</span></div>`;
  for (const k of keys) {
    const d = state.settings[k];
    const ability = d.ability || [];
    const text = k in TEXT_SETTINGS;
    const row = document.createElement("button");
    row.className = "row" + (ability.length > 1 || text ? "" : " locked");
    row.innerHTML = `<span>${SETTING_NAMES[k]}</span><span class="val">${text ? d.value : label(k, d.value)}</span>`;
    if (text) row.onclick = () => editText(k, d);
    else if (ability.length > 1) row.onclick = () => pick(k, d);
    else row.title = "Locked in the camera's current mode";
    list.appendChild(row);
  }
  if ((state.caps || []).includes("focus_drive")) list.appendChild(focusRow());
}

// Manual focus nudge (USB): ‹ nearer, › farther; more arrows = bigger step
function focusRow() {
  const row = document.createElement("div");
  row.className = "row focusdrive";
  row.innerHTML = `<span>Focus</span><span class="steps"></span>`;
  for (const [step, t] of [[-3, "‹‹‹"], [-2, "‹‹"], [-1, "‹"], [1, "›"], [2, "››"], [3, "›››"]]) {
    const b = document.createElement("button");
    b.textContent = t;
    b.setAttribute("aria-label", `${step < 0 ? "Nearer" : "Farther"} ${Math.abs(step)}`);
    b.onclick = async () => {
      haptic();
      try { await api("focus", { step }); } catch (e) { toast(`Focus: ${e.message}`); }
    };
    row.querySelector(".steps").appendChild(b);
  }
  return row;
}

async function editText(key, d) {
  const n = TEXT_SETTINGS[key];
  const v = prompt(`${SETTING_NAMES[key]} (${n} letters/digits)`, d.value);
  if (v === null) return;
  if (v.length !== n) return toast(`${SETTING_NAMES[key]} needs exactly ${n} characters`);
  try { await api("settings/" + key, { value: v.toUpperCase() }, "PUT"); }
  catch (e) { toast(`Couldn't set ${SETTING_NAMES[key]}: ${e.message}`); }
}

const QUICK = { movie_size: "Movie", tv: "Shutter", av: "Aperture", iso: "ISO", exposure: "Exposure", wb: "WB" };
function renderQuick() {
  const box = $("quick");
  box.innerHTML = "";
  // Look: preview Canon Log as graded Rec.709 (the camera keeps recording log)
  const look = document.createElement("button");
  look.className = "q" + (lutOn ? " on" : "");
  look.innerHTML = `<small>Look</small><b>${lutOn ? "Rec.709" : "Log (flat)"}</b>`;
  look.onclick = () => {
    lutOn = !lutOn;
    try { localStorage.setItem("lut", lutOn ? "clog3" : ""); } catch (e) { /* not persisted */ }
    haptic();
    toast(lutOn ? "Previewing the graded look – recording stays Canon Log" : "Showing the flat log picture");
    renderQuick();
  };
  box.appendChild(look);
  $("lutbadge").classList.toggle("hidden", !lutOn);
  for (const [k, name] of Object.entries(QUICK)) {
    const d = state.settings?.[k];
    if (!d) continue;
    const open = (d.ability || []).length > 1;
    const b = document.createElement("button");
    b.className = "q" + (open ? "" : " locked");
    b.innerHTML = `<small>${name}</small><b>${label(k, d.value)}</b>`;
    b.onclick = () => open ? pick(k, d) : toast(`${SETTING_NAMES[k]} is fixed in the camera's current mode`);
    box.appendChild(b);
  }
}

function pick(key, d) {
  $("sheet-title").textContent = SETTING_NAMES[key];
  const box = $("sheet-options");
  box.innerHTML = "";
  for (const v of d.ability) {
    const b = document.createElement("button");
    b.className = "option" + (v === d.value ? " current" : "");
    b.textContent = label(key, v);
    b.onclick = async () => {
      closeSheet();
      haptic();
      try {
        await api("settings/" + key, { value: v }, "PUT");
      } catch (e) { toast(`Couldn't set ${SETTING_NAMES[key]}: ${e.message}`); }
    };
    box.appendChild(b);
  }
  $("sheet").classList.add("show");
}
const closeSheet = () => $("sheet").classList.remove("show");
$("sheet-cancel").onclick = closeSheet;
$("sheet").onclick = (e) => { if (e.target === $("sheet")) closeSheet(); };

// ---------- shared state: long-poll the hub's event log
async function follow() {
  for (;;) {
    try {
      const r = await api(`events?since=${seq}&timeout=25`);
      if (r.reset) { state = { ...state, ...r.state }; seq = r.seq; render(); continue; }
      for (const ev of r.events) apply(ev);
      seq = r.seq;
    } catch (e) {
      if (String(e.message).includes("pair")) { location.reload(); return; }
      state.connection = "Hub unreachable – retrying…";
      render();
      await new Promise((ok) => setTimeout(ok, 2000));
      await loadState();
    }
  }
}
function apply(ev) {
  for (const k of ["status", "settings", "effective"]) if (ev[k]) state[k] = { ...state[k], ...ev[k] };
  for (const k of ["connection", "info", "caps"]) if (ev[k] !== undefined) state[k] = ev[k];
  if ("recording" in ev) {
    state.recording = ev.recording;
    if (ev.by && ev.by !== DEVICE) toast(ev.recording ? `Recording started on ${ev.by}` : `Recording stopped on ${ev.by}`);
  }
  if (ev.type === "shoot" && ev.by !== DEVICE) toast(`Photo taken on ${ev.by}`);
  if (ev.added?.length) {
    toast(`New on card: ${ev.added.map((p) => p.split("/").pop()).join(", ")}`);
    if (filesLoaded) loadFiles();
  }
  render();
}
async function loadState() {
  try {
    const s = await api("state");
    state = { ...state, ...s };
    state.clockSkew = s.server_time - Date.now() / 1000;
    seq = s.seq;
    render();
  } catch (e) { /* follow() retries */ }
}

// ---------- live view: fetch frames in a loop while visible (works on every browser, unlike MJPEG <img>)
let liveOn = false, lastUrl = null;
let lutOn = false;
try { lutOn = localStorage.getItem("lut") === "clog3"; } catch (e) { /* private mode: default off */ }
async function liveLoop() {
  if (liveOn) return;
  liveOn = true;
  const img = $("live");
  let fails = 0;
  while (!document.hidden) {
    const t0 = performance.now();
    try {
      const r = await fetch("/api/live.jpg" + (lutOn ? "?lut=clog3" : ""), { cache: "no-store", headers: { "X-Studio-Device": DEVICE } });
      if (!r.ok) throw new Error(r.status);
      const url = URL.createObjectURL(await r.blob());
      await new Promise((ok) => { img.onload = ok; img.onerror = ok; img.src = url; });
      if (lastUrl) URL.revokeObjectURL(lastUrl);
      lastUrl = url;
      $("empty").classList.add("hidden");
      fails = 0;
    } catch (e) {
      if (++fails > 2) $("empty").classList.remove("hidden");
      await new Promise((ok) => setTimeout(ok, 1000));
    }
    await new Promise((ok) => setTimeout(ok, Math.max(0, 80 - (performance.now() - t0))));  // ~12 fps
  }
  liveOn = false;
}
const startLive = liveLoop;
document.addEventListener("visibilitychange", () => { if (!document.hidden) liveLoop(); });

// tap to focus
$("stage").addEventListener("click", async (e) => {
  const img = $("live");
  if (!img.naturalWidth) return;
  const box = img.getBoundingClientRect();
  const scale = Math.min(box.width / img.naturalWidth, box.height / img.naturalHeight);
  const w = img.naturalWidth * scale, h = img.naturalHeight * scale;
  const x = (e.clientX - box.left - (box.width - w) / 2) / w;
  const y = (e.clientY - box.top - (box.height - h) / 2) / h;
  if (x < 0 || x > 1 || y < 0 || y > 1) return;
  const ring = $("focusring");
  ring.style.left = `${e.clientX - box.left}px`;
  ring.style.top = `${e.clientY - box.top}px`;
  ring.classList.remove("show"); void ring.offsetWidth; ring.classList.add("show");
  haptic();
  try { await api("focus", { x, y }); } catch (err) { toast("Focus: " + err.message); }
});

// hold to zoom
for (const b of document.querySelectorAll("[data-zoom]")) {
  const start = (e) => { e.preventDefault(); b.classList.add("held"); haptic(); api("zoom", { direction: b.dataset.zoom }).catch((err) => toast("Zoom: " + err.message)); };
  const stop = () => { if (!b.classList.contains("held")) return; b.classList.remove("held"); api("zoom", { direction: "stop" }).catch(() => {}); };
  b.addEventListener("pointerdown", start);
  for (const ev of ["pointerup", "pointerleave", "pointercancel"]) b.addEventListener(ev, stop);
}

// buttons
$("recbtn").onclick = async () => {
  haptic();
  try { await api("record", { start: !state.recording }); } catch (e) { toast("Record: " + e.message); }
};
$("shoot").onclick = async () => {
  haptic();
  $("stage").animate([{ opacity: 1 }, { opacity: .2 }, { opacity: 1 }], { duration: 180 });
  try { await api("shoot", {}); } catch (e) { toast(e.message); }
};
$("af").onclick = async () => { haptic(); try { await api("focus", {}); toast("Focusing"); } catch (e) { toast("AF: " + e.message); } };

// tabs
for (const t of document.querySelectorAll(".tab")) {
  t.onclick = () => {
    for (const x of document.querySelectorAll(".tab")) x.classList.toggle("active", x === t);
    for (const v of document.querySelectorAll(".view")) v.classList.toggle("active", v.id === "view-" + t.dataset.view);
    if (t.dataset.view === "files" && !filesLoaded) loadFiles();
  };
}

// files
let filesLoaded = false;
async function loadFiles() {
  filesLoaded = true;
  const grid = $("files");
  try {
    const files = (await api("files")).slice().reverse();
    $("filecount").textContent = `${files.length} files on the card`;
    grid.innerHTML = "";
    const io = new IntersectionObserver((entries) => {
      for (const en of entries) if (en.isIntersecting) {
        const img = en.target.querySelector("img");
        img.src = "/api/thumb?path=" + encodeURIComponent(en.target.dataset.path);
        io.unobserve(en.target);
      }
    }, { rootMargin: "200px" });
    for (const p of files) {
      const name = p.split("/").pop();
      const ext = name.split(".").pop().toUpperCase();
      const a = document.createElement("a");
      a.className = "tile";
      a.dataset.path = p;
      a.href = "/api/file?path=" + encodeURIComponent(p);
      a.innerHTML = `<img alt="${name}" onload="this.classList.add('loaded')"><span class="badge">${ext === "JPG" ? "" : ext}</span>`;
      if (ext === "JPG") a.querySelector(".badge").remove();
      grid.appendChild(a);
      io.observe(a);
    }
  } catch (e) { $("filecount").textContent = "Files unavailable: " + e.message; }
}
$("reload").onclick = loadFiles;

if ("serviceWorker" in navigator) navigator.serviceWorker.register("/sw.js").catch(() => {});
loadState().then(() => { follow(); startLive(); });
