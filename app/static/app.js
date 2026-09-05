const $ = (sel) => document.querySelector(sel);
const $$ = (sel) => document.querySelectorAll(sel);
const COLORS = ["#4f8ef7", "#34c38f", "#f1b44c", "#e8544f", "#9b6ef3", "#55c1e9"];

function colorFor(name) {
  let h = 0;
  const s = String(name ?? "");
  for (let i = 0; i < s.length; i++) h = (h * 31 + s.charCodeAt(i)) >>> 0;
  return COLORS[h % COLORS.length];
}

function cssVar(name, fallback) {
  const v = getComputedStyle(document.documentElement).getPropertyValue(name).trim();
  return v || fallback;
}

const state = { devices: [], dataReadings: [], uploads: [], dataPage: 0, dataSort: { key: "timestamp", dir: -1 } };
const charts = {};
const PAGE_SIZE = 50;

/* ---------- theme (vanilla, no deps) ---------- */
function initTheme() {
  try {
    const saved = localStorage.getItem("bt-theme");
    if (saved) document.documentElement.dataset.theme = saved;
  } catch (err) { /* ignore */ }
  const btn = $("#theme-toggle");
  if (btn) btn.addEventListener("click", () => {
    const cur = document.documentElement.dataset.theme;
    const next = cur === "dark" ? "light" : "dark";
    document.documentElement.dataset.theme = next;
    try { localStorage.setItem("bt-theme", next); } catch (err) { /* ignore */ }
    loadDashboard().catch((e) => toast(e.message, true));
  });
}

async function api(path, options = {}) {
  const res = await fetch(path, options);
  if (!res.ok) {
    let detail = res.statusText;
    try { const body = await res.json(); detail = body.detail || detail; } catch (err) { /* ignore */ }
    throw new Error(detail);
  }
  return res.json();
}

function toast(message, isError = false) {
  const wrap = $("#toasts");
  if (!wrap) return;
  const el = document.createElement("div");
  el.className = "toast" + (isError ? " error" : "");
  el.textContent = message;
  wrap.appendChild(el);
  requestAnimationFrame(() => el.classList.add("show"));
  setTimeout(() => { el.classList.remove("show"); setTimeout(() => el.remove(), 250); }, 3500);
  while (wrap.children.length > 4) wrap.firstChild.remove();
}

function esc(text) {
  return String(text ?? "").replace(/[&<>"']/g, (c) => ({
    "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;",
  }[c]));
}

function highlightMatch(name, q) {
  const label = String(name ?? "");
  const query = String(q ?? "").trim().toLowerCase();
  if (!query) return esc(label);
  const idx = label.toLowerCase().indexOf(query);
  if (idx < 0) return esc(label);
  return esc(label.slice(0, idx)) + "<mark>" + esc(label.slice(idx, idx + query.length)) + "</mark>" + esc(label.slice(idx + query.length));
}

function rankDevices(query, limit = 6) {
  const q = String(query ?? "").trim().toLowerCase();
  if (!q) return [];
  const hits = (state.devices || []).filter((d) => String(d.name || "").toLowerCase().includes(q));
  hits.sort((a, b) => {
    const an = String(a.name || "").toLowerCase();
    const bn = String(b.name || "").toLowerCase();
    const aStart = an.startsWith(q) ? 0 : 1;
    const bStart = bn.startsWith(q) ? 0 : 1;
    if (aStart !== bStart) return aStart - bStart;
    return an.localeCompare(bn);
  });
  return hits.slice(0, limit);
}

/* Generic autocomplete dropdown: shows closest device names (optionally filenames).
   Option A behavior: picking a suggestion only fills the search box; live filtering
   keeps working as before. */
function attachSearchSuggest({ inputId, boxId, getFiles = null, onPick }) {
  const input = document.getElementById(inputId);
  const box = document.getElementById(boxId);
  if (!input || !box) return;
  let items = [];
  let active = -1;

  function close() {
    box.hidden = true;
    input.setAttribute("aria-expanded", "false");
    active = -1;
  }

  function pick(value) {
    input.value = value;
    close();
    input.focus();
    if (onPick) onPick(value);
    // Keep native search-clear (x) behavior consistent.
    input.dispatchEvent(new Event("input", { bubbles: true }));
  }

  function render() {
    const q = input.value;
    const devices = rankDevices(q, 5);
    let files = [];
    if (getFiles) {
      const qq = String(q ?? "").trim().toLowerCase();
      if (qq) {
        const seen = new Set();
        for (const u of state.uploads || []) {
          const fn = String(u.filename || "");
          if (!fn.toLowerCase().includes(qq) || seen.has(fn)) continue;
          seen.add(fn);
          files.push(fn);
          if (files.length >= 3) break;
        }
        files.sort((a, b) => {
          const al = a.toLowerCase();
          const bl = b.toLowerCase();
          const aS = al.startsWith(qq) ? 0 : 1;
          const bS = bl.startsWith(qq) ? 0 : 1;
          if (aS !== bS) return aS - bS;
          return al.localeCompare(bl);
        });
      }
    }
    items = [
      ...devices.map((d) => ({ kind: "device", value: d.name })),
      ...files.map((f) => ({ kind: "file", value: f })),
    ];
    if (!String(q).trim() || !items.length) { close(); return; }
    box.innerHTML = "";
    let itemIdx = -1;
    const addSection = (title) => {
      const s = document.createElement("div");
      s.className = "suggest-section";
      s.textContent = title;
      box.appendChild(s);
    };
    const addItem = (item) => {
      itemIdx += 1;
      const idx = itemIdx;
      const btn = document.createElement("button");
      btn.type = "button";
      btn.setAttribute("role", "option");
      btn.dataset.idx = String(idx);
      if (item.kind === "device") {
        const dev = devices.find((d) => d.name === item.value);
        btn.innerHTML = `<span class="dot" style="background:${colorFor(item.value)}"></span><span>${highlightMatch(item.value, q)}</span>` +
          (dev && dev.readings_count != null ? `<span class="suggest-meta">${dev.readings_count} readings</span>` : "");
      } else {
        btn.innerHTML = `<span>📄 ${highlightMatch(item.value, q)}</span>`;
      }
      btn.addEventListener("mousedown", (e) => { e.preventDefault(); pick(item.value); });
      btn.addEventListener("mouseenter", () => setActive(idx));
      box.appendChild(btn);
    };
    if (devices.length) {
      if (files.length) addSection("Devices");
      devices.forEach((d) => addItem({ kind: "device", value: d.name }));
    }
    if (files.length) {
      addSection("Files");
      files.forEach((f) => addItem({ kind: "file", value: f }));
    }
    box.hidden = false;
    input.setAttribute("aria-expanded", "true");
    setActive(0);
  }

  function setActive(idx) {
    active = idx;
    for (const btn of box.querySelectorAll("button")) {
      const on = Number(btn.dataset.idx) === idx;
      btn.classList.toggle("active", on);
      btn.setAttribute("aria-selected", on ? "true" : "false");
      if (on) btn.scrollIntoView({ block: "nearest" });
    }
  }

  input.addEventListener("input", render);
  input.addEventListener("focus", render);
  input.addEventListener("blur", () => setTimeout(close, 120));
  input.addEventListener("keydown", (e) => {
    if (box.hidden) return;
    if (e.key === "ArrowDown") { e.preventDefault(); setActive((active + 1) % items.length); }
    else if (e.key === "ArrowUp") { e.preventDefault(); setActive((active - 1 + items.length) % items.length); }
    else if (e.key === "Enter") {
      if (active >= 0 && items[active]) { e.preventDefault(); pick(items[active].value); }
    } else if (e.key === "Escape") { e.preventDefault(); close(); }
  });
  document.addEventListener("click", (e) => {
    if (!box.hidden && !e.target.closest(".search-wrap")) close();
  });
}

/* Native <dialog> replacement for prompt()/confirm() */
function confirmDialog({ title = "Confirm", text = "", inputValue = null, okLabel = "Confirm", danger = false } = {}) {
  const dlg = $("#confirm-modal");
  if (!dlg || typeof dlg.showModal !== "function") {
    if (inputValue !== null) return Promise.resolve(prompt(text, inputValue));
    return Promise.resolve(confirm(text));
  }
  $("#confirm-title").textContent = title;
  $("#confirm-text").textContent = text;
  const wrap = $("#confirm-input-wrap");
  const input = $("#confirm-input");
  const ok = $("#confirm-ok");
  if (inputValue !== null) { wrap.hidden = false; input.value = inputValue; } else { wrap.hidden = true; input.value = ""; }
  ok.textContent = okLabel;
  ok.className = danger ? "btn small danger" : "btn small";
  return new Promise((resolve) => {
    const cancel = $("#confirm-cancel");
    const onClose = () => { dlg.removeEventListener("close", onClose); resolve(dlg.returnValue === "ok" ? (inputValue !== null ? input.value : true) : null); };
    dlg.addEventListener("close", onClose);
    cancel.onclick = () => { dlg.returnValue = "cancel"; dlg.close(); };
    ok.onclick = () => { dlg.returnValue = "ok"; dlg.close(); };
    dlg.showModal();
    if (inputValue !== null) setTimeout(() => { input.focus(); input.select(); }, 50);
  });
}

/* ---------- tabs ---------- */
$("#tabs").addEventListener("click", (event) => {
  const btn = event.target.closest(".tab-btn");
  if (!btn) return;
  for (const b of $$(".tab-btn")) { b.classList.toggle("active", b === btn); b.setAttribute("aria-selected", b === btn ? "true" : "false"); }
  for (const section of $$(".tab")) section.classList.remove("active");
  $("#tab-" + btn.dataset.tab).classList.add("active");
});

/* ---------- devices ---------- */
async function loadDevices() {
  state.devices = await api("/api/devices");
  renderDeviceSelects();
  renderDevicesTable();
  renderExportList();
  const badge = $("#device-count");
  if (badge) badge.textContent = `${state.devices.length} device${state.devices.length === 1 ? "" : "s"}`;
  const help = $("#first-run-help");
  if (help) help.hidden = state.devices.length > 0;
}

function renderDeviceSelects() {
  for (const sel of [$("#dash-device"), $("#data-device"), $("#upload-filter")]) {
    if (!sel) continue;
    const current = sel.value;
    sel.innerHTML = "";
    sel.add(new Option("All devices", "all"));
    for (const device of state.devices) sel.add(new Option(device.name, device.id));
    if ([...sel.options].some((o) => o.value === current)) sel.value = current;
  }
  const uploadSel = $("#upload-device");
  const currentUpload = uploadSel.value;
  uploadSel.innerHTML = "";
  if (!state.devices.length) {
    uploadSel.add(new Option("create a device first", ""));
  } else {
    for (const device of state.devices) uploadSel.add(new Option(device.name, device.id));
    if ([...uploadSel.options].some((o) => o.value === currentUpload)) uploadSel.value = currentUpload;
  }
}

function renderDevicesTable() {
  const tbody = $("#devices-table tbody");
  tbody.innerHTML = "";
  if (!state.devices.length) {
    tbody.innerHTML = `<tr><td colspan="5"><div class="empty-state"><h3>No devices yet</h3><p class="muted">Add your first iPhone or iPad above.</p></div></td></tr>`;
    return;
  }
  for (const device of state.devices) {
    const tr = document.createElement("tr");
    tr.innerHTML = `
      <td><span class="dot" style="background:${colorFor(device.name)}"></span>${esc(device.name)}</td>
      <td>${device.readings_count}</td>
      <td>${device.latest_reading ? esc(device.latest_reading.slice(0, 10)) : "-"}</td>
      <td><input type="checkbox" title="Hide all readings from graphs" aria-label="Hide ${esc(device.name)} from graphs" data-act="toggle-device-hidden" data-id="${device.id}" ${device.is_excluded ? "checked" : ""}></td>
      <td>
        <button class="btn small subtle" data-act="rename-device" data-id="${device.id}">Rename</button>
        <button class="btn small danger" data-act="delete-device" data-id="${device.id}">Delete</button>
      </td>`;
    tbody.appendChild(tr);
  }
}

$("#devices-table").addEventListener("click", async (event) => {
  const btn = event.target.closest("button[data-act]");
  if (!btn) return;
  const id = btn.dataset.id;
  if (btn.dataset.act === "rename-device") {
    const device = state.devices.find((d) => String(d.id) === id);
    const name = await confirmDialog({ title: "Rename device", text: "New device name:", inputValue: device ? device.name : "", okLabel: "Rename" });
    if (!name || !name.trim()) return;
    try {
      await api(`/api/devices/${id}`, { method: "PATCH", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ name: name.trim() }) });
      await refreshAll();
    } catch (err) { toast(err.message, true); }
  }
  if (btn.dataset.act === "delete-device") {
    const ok = await confirmDialog({ title: "Delete device?", text: "Delete this device and ALL of its readings? This cannot be undone.", okLabel: "Delete", danger: true });
    if (!ok) return;
    try {
      await api(`/api/devices/${id}`, { method: "DELETE" });
      await refreshAll();
      toast("Device deleted");
    } catch (err) { toast(err.message, true); }
  }
});

$("#devices-table").addEventListener("change", async (event) => {
  const input = event.target.closest("input[data-act='toggle-device-hidden']");
  if (!input) return;
  try {
    await api(`/api/devices/${input.dataset.id}`, { method: "PATCH", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ is_excluded: input.checked }) });
    await refreshAll();
  } catch (err) { toast(err.message, true); }
});

async function addDeviceFromInput() {
  const input = $("#device-name");
  const name = input.value.trim();
  if (!name) { toast("Enter a device name", true); return; }
  try {
    const device = await api("/api/devices", { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ name }) });
    input.value = "";
    await refreshAll();
    toast(`Device "${device.name}" created`);
  } catch (err) { toast(err.message, true); }
}

$("#add-device-btn").addEventListener("click", addDeviceFromInput);
$("#device-name").addEventListener("keydown", (e) => { if (e.key === "Enter") addDeviceFromInput(); });

/* "+ New" button next to the upload device selector */
$("#new-device-btn").addEventListener("click", async () => {
  const name = await confirmDialog({ title: "New device", text: "Name of the new device (e.g. iPhone 15 Pro):", inputValue: "", okLabel: "Create" });
  if (!name || !name.trim()) return;
  try {
    const device = await api("/api/devices", { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ name: name.trim() }) });
    await refreshAll();
    $("#upload-device").value = String(device.id);
    toast(`Device "${device.name}" created`);
  } catch (err) { toast(err.message, true); }
});

/* ---------- upload (native drag-drop + XHR progress, same /api/upload) ---------- */
function renderFileList() {
  const list = $("#file-list");
  if (!list) return;
  const files = $("#file-input").files;
  list.innerHTML = "";
  [...files].forEach((f) => {
    const li = document.createElement("li");
    li.innerHTML = `<span>${esc(f.name)}</span><span class="muted">${(f.size / 1024).toFixed(1)} KB</span>`;
    list.appendChild(li);
  });
}

function uploadWithProgress(form) {
  return new Promise((resolve, reject) => {
    const xhr = new XMLHttpRequest();
    xhr.open("POST", "/api/upload");
    const bar = $("#upload-progress");
    xhr.upload.onprogress = (e) => { if (bar && e.lengthComputable) { bar.hidden = false; bar.value = Math.round((e.loaded / e.total) * 100); } };
    xhr.onload = () => {
      if (bar) bar.hidden = true;
      try {
        const data = JSON.parse(xhr.responseText);
        if (xhr.status >= 200 && xhr.status < 300) resolve(data);
        else reject(new Error(data.detail || xhr.statusText));
      } catch (err) { reject(err); }
    };
    xhr.onerror = () => reject(new Error("Upload failed"));
    xhr.send(form);
  });
}

async function doUpload() {
  const deviceId = $("#upload-device").value;
  if (!deviceId) { toast("Create a device first", true); return; }
  const files = $("#file-input").files;
  if (!files.length) { toast("Choose one or more .ips files (or drag & drop, or paste below)", true); return; }
  for (const file of files) {
    if (file.size === 0) { toast(`"${file.name}" looks empty — skipped empty files`, true); return; }
  }
  const form = new FormData();
  form.append("device_id", deviceId);
  for (const file of files) form.append("files", file);
  $("#upload-results").innerHTML = "<p class='muted'>Uploading…</p>";
  try {
    const data = await uploadWithProgress(form);
    renderUploadResults(data.results);
    $("#file-input").value = "";
    renderFileList();
    await refreshAll();
  } catch (err) {
    $("#upload-results").innerHTML = "";
    toast(err.message, true);
  }
}

async function doPasteUpload() {
  const deviceId = $("#upload-device") ? $("#upload-device").value : "";
  if (!deviceId) { toast("Create a device first", true); return; }
  const content = $("#paste-input") ? $("#paste-input").value : "";
  if (!content.trim()) { toast("Paste .ips file content first", true); return; }
  const filename = $("#paste-filename") && $("#paste-filename").value.trim()
    ? $("#paste-filename").value.trim()
    : `pasted-${new Date().toISOString().slice(0, 10)}.ips`;
  const box = $("#paste-results");
  if (box) box.innerHTML = "<p class='muted'>Uploading pasted text…</p>";
  try {
    const data = await api("/api/upload-text", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ device_id: Number(deviceId), filename, content }),
    });
    if (box) {
      box.innerHTML = "";
      for (const result of data.results) {
        const line = document.createElement("p");
        if (result.status === "duplicate") {
          line.innerHTML = `<span class="badge dup">duplicate</span> ${esc(result.filename)} — already uploaded for this device, skipped.`;
        } else {
          line.innerHTML = `<span class="badge ok">${result.new_readings} new</span> <span class="badge info">${result.readings_found} found</span> ${esc(result.filename)}` +
            (result.updated_readings ? `, ${result.updated_readings} updated` : "") + ".";
        }
        box.appendChild(line);
      }
    }
    if ($("#paste-input")) $("#paste-input").value = "";
    await refreshAll();
    toast("Pasted text imported");
  } catch (err) {
    if (box) box.innerHTML = "";
    toast(err.message, true);
  }
}

$("#upload-btn").addEventListener("click", doUpload);
const _pasteBtn = $("#paste-btn");
if (_pasteBtn) _pasteBtn.addEventListener("click", doPasteUpload);
$("#file-input").addEventListener("change", renderFileList);
(function initDropZone() {
  const zone = $("#drop-zone");
  const input = $("#file-input");
  if (!zone || !input) return;
  ["dragenter", "dragover"].forEach((ev) => zone.addEventListener(ev, (e) => { e.preventDefault(); zone.classList.add("dragover"); }));
  ["dragleave", "drop"].forEach((ev) => zone.addEventListener(ev, (e) => { e.preventDefault(); zone.classList.remove("dragover"); }));
  zone.addEventListener("drop", (e) => {
    if (e.dataTransfer && e.dataTransfer.files.length) { input.files = e.dataTransfer.files; renderFileList(); }
  });
})();

function renderUploadResults(results) {
  const box = $("#upload-results");
  box.innerHTML = "";
  for (const result of results) {
    const line = document.createElement("p");
    if (result.status === "duplicate") {
      line.innerHTML = `<span class="badge dup">duplicate</span> ${esc(result.filename)} — already uploaded for this device, skipped.`;
    } else {
      line.innerHTML = `<span class="badge ok">${result.new_readings} new</span> <span class="badge info">${result.readings_found} found</span> ${esc(result.filename)}` +
        (result.updated_readings ? `, ${result.updated_readings} updated` : "") + "." +
        (result.unrecognized_keys && result.unrecognized_keys.length ? ` <span class="muted">New keys: ${esc(result.unrecognized_keys.join(", "))}</span>` : "");
      if (result.cross_device_duplicate && result.cross_device_duplicate.length) {
        const warn = document.createElement("p");
        warn.innerHTML = `<span class="badge warn">check device</span> Same file already uploaded for: ${esc(result.cross_device_duplicate.join(", "))} — did you pick the right device?`;
        box.appendChild(line);
        box.appendChild(warn);
        continue;
      }
    }
    box.appendChild(line);
  }
}

async function renderUploadHistory() {
  const filter = $("#upload-filter") ? $("#upload-filter").value : "all";
  const qs = filter && filter !== "all" ? `?device_id=${encodeURIComponent(filter)}` : "";
  state.uploads = await api(`/api/uploads${qs}`);
  paintUploadHistory();
}

function paintUploadHistory() {
  const q = ($("#upload-search") ? $("#upload-search").value : "").toLowerCase();
  const rows = state.uploads.filter((u) => !q || u.filename.toLowerCase().includes(q) || (u.device_name || "").toLowerCase().includes(q));
  const tbody = $("#upload-history tbody");
  tbody.innerHTML = "";
  if (!rows.length) {
    tbody.innerHTML = `<tr><td colspan="5"><div class="empty-state"><h3>No uploads match</h3><p class="muted">${state.uploads.length ? "Try a different search." : "Upload an .ips file above to get started."}</p></div></td></tr>`;
    return;
  }
  for (const upload of rows) {
    const tr = document.createElement("tr");
    tr.innerHTML = `
      <td title="${esc(upload.uploaded_at ?? "")}">${esc(fmtDate(upload.uploaded_at))}</td>
      <td>${esc(upload.filename)}</td>
      <td><span class="dot" style="background:${colorFor(upload.device_name)}"></span>${esc(upload.device_name)}</td>
      <td>${upload.readings_count}</td>
      <td><button class="btn small danger" data-act="delete-upload" data-id="${upload.id}">Delete</button></td>`;
    tbody.appendChild(tr);
  }
}

$("#upload-history").addEventListener("click", async (event) => {
  const btn = event.target.closest("button[data-act='delete-upload']");
  if (!btn) return;
  const ok = await confirmDialog({ title: "Delete upload?", text: "Delete this upload and its recorded readings?", okLabel: "Delete", danger: true });
  if (!ok) return;
  try {
    await api(`/api/uploads/${btn.dataset.id}`, { method: "DELETE" });
    await refreshAll();
    toast("Upload deleted");
  } catch (err) { toast(err.message, true); }
});

/* ---------- dashboard ---------- */
function toMs(ts) {
  const t = new Date(ts).getTime();
  return Number.isFinite(t) ? t : null;
}

function fmtDate(ts) {
  const d = new Date(ts);
  return Number.isFinite(d.getTime()) ? d.toLocaleString() : "-";
}

function plural(n, singular, pluralForm) {
  const word = n === 1 ? singular : (pluralForm || singular + "s");
  return `${n} ${word}`;
}

function buildDatasets(readings, field, opts = {}) {
  const byDevice = new Map();
  for (const r of readings) {
    if (r[field] == null) continue;
    const x = toMs(r.timestamp);
    if (x == null) continue;
    if (!byDevice.has(r.device_name)) byDevice.set(r.device_name, []);
    byDevice.get(r.device_name).push({ x, y: r[field], soc: r.battery_level_pct });
  }
  return [...byDevice.entries()]
    .sort((a, b) => a[0].localeCompare(b[0]))
    .map(([name, points]) => ({
      label: opts.labelSuffix ? `${name}${opts.labelSuffix}` : name,
      data: points.sort((a, b) => a.x - b.x),
      borderColor: opts.dashed ? colorFor(name) + "99" : colorFor(name),
      backgroundColor: colorFor(name),
      borderWidth: 2,
      pointRadius: 3.5,
      pointHoverRadius: 6,
      showLine: true,
      spanGaps: true,
      tension: 0.2,
      borderDash: opts.dashed ? [6, 4] : [],
    }));
}

function xRangeFor(readings, clampMin = null) {
  let min = Infinity, max = -Infinity;
  for (const r of readings || []) {
    const x = toMs(r.timestamp);
    if (x == null) continue;
    if (x < min) min = x;
    if (x > max) max = x;
  }
  if (!Number.isFinite(min) || !Number.isFinite(max)) return {};
  const DAY = 86400000;
  let out;
  if (min === max) out = { min: min - 30 * DAY, max: max + 30 * DAY };
  else {
    const span = max - min;
    const pad = Math.max(span * 0.05, DAY);
    out = { min: min - pad, max: max + pad };
  }
  if (clampMin != null && Number.isFinite(clampMin)) out.min = Math.max(out.min, clampMin);
  return out;
}

function getSelectedRangeDays() {
  const active = document.querySelector('[data-range].active');
  if (!active) return null;
  const days = Number(active.dataset.range);
  return Number.isFinite(days) && days > 0 ? days : null;
}

function getTimeFilter() {
  const days = getSelectedRangeDays();
  if (days == null) return { active: false, fromMs: null, enabled: false, days: null };
  const d = new Date();
  d.setHours(0, 0, 0, 0);
  const fromMs = d.getTime() - days * 86400000;
  return { active: true, fromMs, enabled: true, days };
}

function paintTimePresets() {
  const days = getSelectedRangeDays();
  for (const btn of $$("[data-range]")) {
    const on = days != null && Number(btn.dataset.range) === days;
    btn.classList.toggle("active", on);
    btn.setAttribute("aria-pressed", on ? "true" : "false");
  }
  const all = $("#time-all");
  if (all) {
    all.classList.toggle("active", days == null);
    all.setAttribute("aria-pressed", days == null ? "true" : "false");
  }
}

function inTimeRange(r, f) {
  if (!f.active) return true;
  const x = toMs(r.timestamp);
  if (x == null) return true;
  return x >= f.fromMs;
}

function baseOptions(yTitle, extraY = {}, showSoc = false, xRange = {}) {
  const grid = cssVar("--border", "#eef1f4");
  return {
    responsive: true,
    maintainAspectRatio: false,
    interaction: { mode: "nearest", intersect: false },
    plugins: {
      legend: { position: "top", labels: { usePointStyle: true, pointStyle: "circle", boxWidth: 8, boxHeight: 8, pointStyleWidth: 8 } },
      tooltip: {
        callbacks: {
          title: (items) => items.length ? new Date(items[0].parsed.x).toLocaleString() : "",
          label: (item) => {
            let s = ` ${item.dataset.label}: ${item.parsed.y}${yTitle.suffix || ""}`;
            if (showSoc && item.raw && item.raw.soc != null) s += ` (@${item.raw.soc}% SoC)`;
            return s;
          },
        },
      },
    },
    scales: {
      x: {
        type: "linear",
        min: xRange.min,
        max: xRange.max,
        ticks: { maxTicksLimit: 8, maxRotation: 0, callback: (v) => new Date(v).toLocaleDateString() },
        grid: { color: grid },
      },
      y: {
        title: { display: true, text: yTitle.text },
        grace: "8%",
        ticks: { maxTicksLimit: 6 },
        grid: { color: grid },
        ...extraY,
      },
    },
  };
}

function getChargeWindow() {
  // Lower-limit outlier filter: hide readings with charge < min.
  // Readings without charge data (null) are always included.
  const enabled = $("#charge-filter-enabled") ? $("#charge-filter-enabled").checked : true;
  const minRaw = $("#charge-min") ? $("#charge-min").value : "";
  if (!enabled) return { active: false, lo: 0, hi: 100, min: null, enabled: false };
  if (minRaw === "") return { active: false, lo: 0, hi: 100, min: null, enabled: true };
  const min = Math.max(0, Math.min(100, Number(minRaw)));
  if (Number.isNaN(min)) return { active: false, lo: 0, hi: 100, min: null, enabled: true };
  return { active: true, lo: min, hi: 100, min, enabled: true };
}

function inChargeWindow(r, win) {
  if (!win.active) return true;
  if (r.battery_level_pct == null) return true;
  return r.battery_level_pct >= win.lo;
}

function setChartEmpty(id, empty) {
  const wrap = document.getElementById(id)?.closest(".chart-wrap");
  if (!wrap) return;
  let el = wrap.querySelector(".chart-empty");
  if (empty && !el) { el = document.createElement("div"); el.className = "chart-empty"; el.textContent = "No points for this filter"; wrap.appendChild(el); }
  if (!empty && el) el.remove();
}

function renderCharts(allReadings, windowedReadings, { normalizeCapacity = false, timeFromMs = null } = {}) {
  const defs = [
    { id: "chart-cycles", field: "cycle_count", rows: allReadings, y: { text: "Cycles" }, yExtra: { beginAtZero: false } },
    {
      id: "chart-capacity",
      field: normalizeCapacity ? "capacity_norm_pct" : "capacity_value",
      rows: windowedReadings, showSoc: true,
      y: { text: normalizeCapacity ? "% of design" : "mAh", suffix: normalizeCapacity ? "%" : "" },
      yExtra: normalizeCapacity ? { min: 50, max: 105 } : { beginAtZero: false },
    },
    {
      id: "chart-health", field: "health_pct", rows: windowedReadings, showSoc: true,
      y: { text: "Health %", suffix: "%" }, yExtra: { min: 50, max: 105 },
    },
  ];
  const xRange = xRangeFor([...allReadings, ...windowedReadings], timeFromMs);
  for (const def of defs) {
    const datasets = buildDatasets(def.rows, def.field);
    const empty = datasets.length === 0;
    setChartEmpty(def.id, empty);
    const el = document.getElementById(def.id);
    if (!el) continue;
    if (empty) {
      // No points: destroy any stale chart instead of rendering one.
      // An empty Chart.js line chart falls back to a 0-1ms x-domain
      // (rendered as 12/31/1969 ticks) and a 0-centered y-range with
      // grace (rendered as negative cycles/mAh). The overlay from
      // setChartEmpty carries the empty state on its own.
      if (charts[def.id]) { charts[def.id].destroy(); delete charts[def.id]; }
      continue;
    }
    if (charts[def.id]) {
      charts[def.id].data.datasets = datasets;
      charts[def.id].options = baseOptions(def.y, def.yExtra, def.showSoc, xRange);
      charts[def.id].update();
      continue;
    }
    charts[def.id] = new Chart(el, {
      type: "line",
      data: { datasets },
      options: baseOptions(def.y, def.yExtra, def.showSoc, xRange),
    });
  }
}

function restoreDashFilters() {
  try {
    const saved = JSON.parse(localStorage.getItem("dash-filters") || "{}");
    if (saved.showHidden != null && $("#show-hidden")) $("#show-hidden").checked = !!saved.showHidden;
    if (saved.norm != null && $("#norm-capacity")) $("#norm-capacity").checked = !!saved.norm;
    if (saved.chargeEnabled != null && $("#charge-filter-enabled")) $("#charge-filter-enabled").checked = !!saved.chargeEnabled;
    const cw = JSON.parse(localStorage.getItem("charge-window") || "{}");
    // Migrate old range format {lo, hi} -> single lower limit {min}.
    const lo = cw.min ?? cw.lo;
    if (lo != null && $("#charge-min")) $("#charge-min").value = lo;
    if ($("#charge-min") && $("#charge-filter-enabled")) $("#charge-min").disabled = !$("#charge-filter-enabled").checked;
    const tw = JSON.parse(localStorage.getItem("time-window") || "{}");
    // Migrate old date format {from: "YYYY-MM-DD"} -> days presets (default All).
    if (tw.days != null && [30, 90, 365].includes(tw.days)) {
      for (const btn of $$("[data-range]")) {
        const on = Number(btn.dataset.range) === tw.days;
        btn.classList.toggle("active", on);
        btn.setAttribute("aria-pressed", on ? "true" : "false");
      }
      const all = $("#time-all");
      if (all) { all.classList.remove("active"); all.setAttribute("aria-pressed", "false"); }
    } else {
      paintTimePresets();
    }
  } catch (err) { /* ignore */ }
}

function saveDashFilters() {
  try {
    localStorage.setItem("dash-filters", JSON.stringify({
      showHidden: $("#show-hidden")?.checked, norm: $("#norm-capacity")?.checked,
      chargeEnabled: $("#charge-filter-enabled")?.checked ?? true,
    }));
  } catch (err) { /* ignore */ }
}

function saveChargeWindow(win) {
  try {
    if (win.active) localStorage.setItem("charge-window", JSON.stringify({ min: win.lo }));
    else localStorage.removeItem("charge-window");
  } catch (err) { /* ignore */ }
}

function saveTimeWindow(f) {
  try {
    if (f.active && f.days != null) localStorage.setItem("time-window", JSON.stringify({ days: f.days }));
    else localStorage.removeItem("time-window");
  } catch (err) { /* ignore */ }
}

async function loadDashboard() {
  const deviceFilter = $("#dash-device").value;
  const qs = deviceFilter && deviceFilter !== "all"
    ? `/api/readings?device_id=${encodeURIComponent(deviceFilter)}&limit=50000`
    : "/api/readings?limit=50000";
  const readings = await api(qs);
  const showHidden = $("#show-hidden").checked;
  const normalizeCapacity = $("#norm-capacity") ? $("#norm-capacity").checked : false;
  const filtered = readings.filter((r) => !r.device_excluded && (showHidden || !r.is_excluded) && r.timestamp && toMs(r.timestamp) != null);
  const withCapacity = filtered.map((r) => {
    const cap = r.full_charge_capacity_mah ?? r.nominal_capacity_mah;
    let norm = null;
    if (cap != null && r.design_capacity_mah) norm = Math.round((100.0 * cap) / r.design_capacity_mah * 100) / 100;
    return { ...r, capacity_value: cap, capacity_norm_pct: norm };
  });
  const win = getChargeWindow();
  const timeF = getTimeFilter();
  const timeFiltered = withCapacity.filter((r) => inTimeRange(r, timeF));
  const windowed = timeFiltered.filter((r) => inChargeWindow(r, win));
  const countEl = $("#charge-count");
  if (countEl) {
    if (win.active) {
      const noCharge = timeFiltered.filter((r) => r.battery_level_pct == null).length;
      countEl.textContent = `${windowed.length}/${timeFiltered.length} ≥${win.lo}% charge` +
        (noCharge ? ` (${noCharge} without charge always shown)` : "");
    } else if (win.enabled === false) {
      countEl.textContent = `${plural(timeFiltered.length, "reading")} (filter off)`;
    } else {
      countEl.textContent = `${plural(timeFiltered.length, "reading")} (no threshold)`;
    }
  }
  const timeCountEl = $("#time-count");
  if (timeCountEl) {
    if (timeF.active) {
      timeCountEl.textContent = `${timeFiltered.length}/${withCapacity.length} last ${timeF.days}d`;
    } else {
      timeCountEl.textContent = withCapacity.length ? `${plural(withCapacity.length, "reading")} (all time)` : `(all time)`;
    }
  }
  // Baseline for "vs" comparison: earliest in-window point per device when
  // time filter is active, else immediate predecessor (existing behavior).
  const baselineByDevice = new Map();
  const fullLatestByDevice = new Map();
  {
    const sortedAll = [...withCapacity].sort((a, b) => String(a.timestamp).localeCompare(String(b.timestamp)));
    for (const r of sortedAll) fullLatestByDevice.set(r.device_name, r);
    if (timeF.active) {
      const sortedWin = [...timeFiltered].sort((a, b) => String(a.timestamp).localeCompare(String(b.timestamp)));
      for (const r of sortedWin) {
        if (!baselineByDevice.has(r.device_name)) baselineByDevice.set(r.device_name, r);
      }
    }
  }
  renderStatsCards(timeFiltered, {
    baselineByDevice: timeF.active ? baselineByDevice : null,
    fullLatestByDevice,
    timeActive: timeF.active,
    timeFromMs: timeF.active ? timeF.fromMs : null,
  });
  renderCharts(timeFiltered, windowed, { normalizeCapacity, timeFromMs: timeF.active ? timeF.fromMs : null });
}

function healthBadge(h) {
  if (h == null) return "";
  const cls = h >= 90 ? "ok" : h >= 80 ? "warn" : "dup";
  return ` <span class="badge ${cls}">${h}%</span>`;
}

function renderStatsCards(readings, opts = {}) {
  const box = $("#stats-cards");
  const byDevice = new Map();
  const sorted = [...readings].sort((a, b) => String(a.timestamp).localeCompare(String(b.timestamp)));
  const prevByDevice = new Map();
  for (const r of sorted) {
    if (byDevice.has(r.device_name)) prevByDevice.set(r.device_name, byDevice.get(r.device_name));
    const existing = byDevice.get(r.device_name);
    if (!existing || r.timestamp > existing.timestamp) byDevice.set(r.device_name, r);
  }
  // When time filter is off, latest comes from the (already filtered) list.
  // When on, keep latest-overall cards even for devices with no in-window
  // data, flagged with "no data in range".
  const fullLatest = opts.fullLatestByDevice || null;
  const timeActive = !!opts.timeActive;
  const baselineByDevice = opts.baselineByDevice || null;
  const displayNames = new Set([...byDevice.keys()]);
  if (timeActive && fullLatest) for (const name of fullLatest.keys()) displayNames.add(name);
  box.innerHTML = "";
  if (!displayNames.size) {
    box.innerHTML = "<p class='muted'>No data yet — upload an analytics file on the Upload tab.</p>";
    return;
  }
  for (const name of [...displayNames].sort((a, b) => a.localeCompare(b))) {
    const latest = byDevice.get(name) || (timeActive && fullLatest ? fullLatest.get(name) : null);
    if (!latest) continue;
    const inRange = byDevice.has(name);
    let prev = prevByDevice.get(name);
    let vsLabel = "vs prev:";
    if (timeActive) {
      const baseline = baselineByDevice ? baselineByDevice.get(name) : null;
      if (!inRange) prev = null;
      else if (baseline && baseline !== latest) {
        prev = baseline;
        vsLabel = `vs ${new Date(baseline.timestamp).toLocaleDateString()}:`;
      } else prev = null;
    }
    const card = document.createElement("div");
    card.className = "card stat";
    card.style.borderTop = `3px solid ${colorFor(name)}`;
    const notes = (latest.is_excluded ? " <span class='badge warn'>hidden</span>" : "") +
      (latest.date_source === "fallback" ? " <span class='badge warn'>fallback date</span>" : "") +
      (timeActive && !inRange ? " <span class='badge warn'>no data in range</span>" : "");
    let delta = "";
    if (prev) {
      const parts = [];
      if (latest.cycle_count != null && prev.cycle_count != null && latest.cycle_count !== prev.cycle_count)
        parts.push(`+${latest.cycle_count - prev.cycle_count} cycles`);
      const cap = latest.full_charge_capacity_mah ?? latest.nominal_capacity_mah;
      const pcap = prev.full_charge_capacity_mah ?? prev.nominal_capacity_mah;
      if (cap != null && pcap != null && cap !== pcap) {
        const d = cap - pcap;
        parts.push(`<span class="${d < 0 ? "bad" : "good"}">${d > 0 ? "+" : ""}${d} mAh</span>`);
      }
      if (parts.length) delta = `<p class="delta">${vsLabel} ${parts.join(" · ")}</p>`;
    }
    const hasCap = latest.full_charge_capacity_mah != null || latest.nominal_capacity_mah != null;
    const designPart = latest.design_capacity_mah != null ? `, design ${latest.design_capacity_mah}` : "";
    const capLine = hasCap
      ? `<strong>${latest.full_charge_capacity_mah ?? "-"}</strong> mAh full <span class="muted">(nominal ${latest.nominal_capacity_mah ?? "-"}${designPart})</span>`
      : `<strong>-</strong> mAh`;
    const chargeLine = latest.battery_level_pct != null
      ? `<p><strong>${latest.battery_level_pct}%</strong> charge at capture</p>`
      : `<p class="muted">No charge data</p>`;
    card.innerHTML = `
      <h3><span class="dot" style="background:${colorFor(name)}"></span>${esc(name)}${notes}</h3>
      <p class="stat-date" title="${esc(latest.timestamp ?? "")}">${esc(fmtDate(latest.timestamp))}</p>
      <p><strong>${latest.cycle_count ?? "-"}</strong> cycles</p>
      <p>${capLine}</p>
      <p>Health ${healthBadge(latest.health_pct) || "-"}</p>
      ${chargeLine}${delta}`;
    box.appendChild(card);
  }
}

["#dash-device", "#show-hidden", "#norm-capacity"].forEach((sel) => $(sel).addEventListener("change", () => { saveDashFilters(); loadDashboard().catch((e) => toast(e.message, true)); }));

/* ---------- data tab (search + sort + pagination, vanilla) ---------- */
async function loadData() {
  const deviceFilter = $("#data-device").value;
  const qs = deviceFilter && deviceFilter !== "all"
    ? `/api/readings?device_id=${encodeURIComponent(deviceFilter)}&limit=50000`
    : "/api/readings?limit=50000";
  let readings = await api(qs);
  if (!$("#data-include-excluded").checked) readings = readings.filter((r) => !r.is_excluded);
  state.dataReadings = readings;
  state.dataPage = 0;
  paintData();
}

function sortedFilteredData() {
  const q = ($("#data-search") ? $("#data-search").value : "").toLowerCase();
  let rows = state.dataReadings.filter((r) =>
    !q || String(r.timestamp || "").toLowerCase().includes(q) || String(r.device_name || "").toLowerCase().includes(q));
  const { key, dir } = state.dataSort;
  rows = [...rows].sort((a, b) => {
    const av = a[key] ?? "";
    const bv = b[key] ?? "";
    if (typeof av === "number" || typeof bv === "number") return ((av ?? -1e18) - (bv ?? -1e18)) * dir;
    return String(av).localeCompare(String(bv)) * dir;
  });
  return rows;
}

function paintData() {
  const rows = sortedFilteredData();
  const pages = Math.max(1, Math.ceil(rows.length / PAGE_SIZE));
  state.dataPage = Math.min(Math.max(0, state.dataPage), pages - 1);
  const slice = rows.slice(state.dataPage * PAGE_SIZE, state.dataPage * PAGE_SIZE + PAGE_SIZE);
  const countEl = $("#readings-count");
  if (countEl) countEl.textContent = plural(rows.length, "reading");
  const info = $("#data-page-info");
  if (info) info.textContent = `Page ${state.dataPage + 1}/${pages}`;
  const prevBtn = $("#data-prev");
  const nextBtn = $("#data-next");
  if (prevBtn) prevBtn.disabled = state.dataPage <= 0;
  if (nextBtn) nextBtn.disabled = state.dataPage >= pages - 1;
  const tbody = $("#readings-table tbody");
  tbody.innerHTML = "";
  if (!slice.length) {
    tbody.innerHTML = `<tr><td colspan="11"><div class="empty-state"><h3>No readings</h3><p class="muted">Try a different filter or search.</p></div></td></tr>`;
    return;
  }
  for (const r of slice) {
    const tr = document.createElement("tr");
    if (r.is_excluded) tr.classList.add("excluded");
    const chargeCell = r.battery_level_pct != null
      ? `${r.battery_level_pct}%${r.min_soc_pct != null && r.max_soc_pct != null ? ` <span class="muted">(day ${r.min_soc_pct}-${r.max_soc_pct}%)</span>` : ""}`
      : "-";
    tr.innerHTML = `
      <td><span class="dot" style="background:${colorFor(r.device_name)}"></span>${esc(r.device_name)}</td>
      <td title="${esc(r.timestamp ?? "")}">${esc(fmtDate(r.timestamp))}${r.date_source === "fallback" ? " <span class='badge warn'>fallback</span>" : ""}</td>
      <td>${r.cycle_count ?? "-"}</td>
      <td>${r.full_charge_capacity_mah ?? "-"}</td>
      <td>${r.nominal_capacity_mah ?? "-"}</td>
      <td>${r.design_capacity_mah ?? "-"}</td>
      <td>${r.health_pct != null ? r.health_pct + "%" : "-"}</td>
      <td title="Instant charge (UISOC) at capture; day range from DailyMin/DailyMax aggregates">${chargeCell}</td>
      <td>${esc(r.date_source)}</td>
      <td class="hide-cell">
        <input type="checkbox" title="Exclude from graphs" aria-label="Exclude reading from graphs" data-act="toggle-reading-hidden" data-id="${r.id}" ${r.is_excluded ? "checked" : ""}>
      </td>
      <td class="actions-cell">
        <button class="btn small danger" data-act="delete-reading" data-id="${r.id}">Delete</button>
      </td>`;
    tbody.appendChild(tr);
  }
  $$("#readings-table th.sortable").forEach((th) => {
    const active = th.dataset.sort === state.dataSort.key;
    th.textContent = th.textContent.replace(/ [▲▼]$/, "") + (active ? (state.dataSort.dir === 1 ? " ▲" : " ▼") : "");
  });
}

$("#readings-table").addEventListener("click", async (event) => {
  const th = event.target.closest("th.sortable");
  if (th) {
    const key = th.dataset.sort;
    if (state.dataSort.key === key) state.dataSort.dir *= -1;
    else state.dataSort = { key, dir: 1 };
    paintData();
    return;
  }
  const btn = event.target.closest("button[data-act='delete-reading']");
  if (!btn) return;
  const ok = await confirmDialog({ title: "Delete reading?", text: "Delete this reading?", okLabel: "Delete", danger: true });
  if (!ok) return;
  try {
    await api(`/api/readings/${btn.dataset.id}`, { method: "DELETE" });
    await refreshAll();
    toast("Reading deleted");
  } catch (err) { toast(err.message, true); }
});

$("#readings-table").addEventListener("change", async (event) => {
  const input = event.target.closest("input[data-act='toggle-reading-hidden']");
  if (!input) return;
  try {
    await api(`/api/readings/${input.dataset.id}`, {
      method: "PATCH",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ is_excluded: input.checked }),
    });
    await refreshAll();
  } catch (err) { toast(err.message, true); }
});

["#data-device", "#data-include-excluded"].forEach((sel) => $(sel).addEventListener("change", loadData));
const _search = $("#data-search");
if (_search) _search.addEventListener("input", () => { state.dataPage = 0; paintData(); });
const _usearch = $("#upload-search");
if (_usearch) _usearch.addEventListener("input", paintUploadHistory);
const _prev = $("#data-prev");
if (_prev) _prev.addEventListener("click", () => { state.dataPage--; paintData(); });
const _next = $("#data-next");
if (_next) _next.addEventListener("click", () => { state.dataPage++; paintData(); });
const _uploadFilter = $("#upload-filter");
if (_uploadFilter) _uploadFilter.addEventListener("change", renderUploadHistory);

attachSearchSuggest({ inputId: "data-search", boxId: "data-search-suggest" });
attachSearchSuggest({ inputId: "upload-search", boxId: "upload-search-suggest", getFiles: true });

function _onChargeInput() {
  saveChargeWindow(getChargeWindow());
  saveDashFilters();
  loadDashboard().catch((err) => toast(err.message, true));
}
for (const sel of ["#charge-min", "#charge-filter-enabled"]) {
  const el = $(sel);
  if (el) el.addEventListener("change", () => {
    if ($("#charge-min") && $("#charge-filter-enabled")) $("#charge-min").disabled = !$("#charge-filter-enabled").checked;
    _onChargeInput();
  });
}
const _chargeClear = $("#charge-clear");
if (_chargeClear) _chargeClear.addEventListener("click", () => {
  if ($("#charge-min")) $("#charge-min").value = "";
  _onChargeInput();
});
function _onTimeInput() {
  paintTimePresets();
  saveTimeWindow(getTimeFilter());
  saveDashFilters();
  loadDashboard().catch((err) => toast(err.message, true));
}
for (const btn of $$("[data-range]")) {
  btn.addEventListener("click", () => {
    for (const b of $$("[data-range]")) {
      const on = b === btn;
      b.classList.toggle("active", on);
      b.setAttribute("aria-pressed", on ? "true" : "false");
    }
    const all = $("#time-all");
    if (all) { all.classList.remove("active"); all.setAttribute("aria-pressed", "false"); }
    _onTimeInput();
  });
}
const _timeAll = $("#time-all");
if (_timeAll) _timeAll.addEventListener("click", () => {
  for (const b of $$("[data-range]")) { b.classList.remove("active"); b.setAttribute("aria-pressed", "false"); }
  _timeAll.classList.add("active");
  _timeAll.setAttribute("aria-pressed", "true");
  _onTimeInput();
});
const _filtersReset = $("#filters-reset");
if (_filtersReset) _filtersReset.addEventListener("click", () => {
  if ($("#dash-device")) $("#dash-device").value = "all";
  if ($("#show-hidden")) $("#show-hidden").checked = false;
  if ($("#norm-capacity")) $("#norm-capacity").checked = false;
  if ($("#charge-filter-enabled")) $("#charge-filter-enabled").checked = true;
  if ($("#charge-min")) { $("#charge-min").value = ""; $("#charge-min").disabled = false; }
  for (const b of $$("[data-range]")) { b.classList.remove("active"); b.setAttribute("aria-pressed", "false"); }
  const all = $("#time-all");
  if (all) { all.classList.add("active"); all.setAttribute("aria-pressed", "true"); }
  try { localStorage.removeItem("charge-window"); localStorage.removeItem("time-window"); localStorage.removeItem("dash-filters"); } catch (err) { /* ignore */ }
  loadDashboard().catch((err) => toast(err.message, true));
});

/* ---------- backup (export / import) ---------- */
state.backupFile = null;

function renderExportList() {
  const box = $("#export-list");
  if (!box) return;
  box.innerHTML = "";
  if (!state.devices.length) {
    box.innerHTML = "<p class='muted'>No devices yet.</p>";
    return;
  }
  for (const d of state.devices) {
    const label = document.createElement("label");
    label.innerHTML = `<input type="checkbox" data-device-id="${d.id}" checked> ${esc(d.name)} <span class="muted">(${d.readings_count})</span>`;
    box.appendChild(label);
  }
}

async function doExport() {
  const checked = [...document.querySelectorAll("#export-list input:checked")].map((el) => el.dataset.deviceId);
  if (!checked.length) { toast("Select at least one device", true); return; }
  try {
    const data = await api(`/api/export?device_ids=${checked.map(encodeURIComponent).join(",")}`);
    const blob = new Blob([JSON.stringify(data, null, 2)], { type: "application/json" });
    const a = document.createElement("a");
    a.href = URL.createObjectURL(blob);
    a.download = `battery-backup-${new Date().toISOString().slice(0, 10)}.json`;
    document.body.appendChild(a);
    a.click();
    setTimeout(() => { URL.revokeObjectURL(a.href); a.remove(); }, 1000);
    toast(`Exported ${data.devices.length} device(s)`);
  } catch (err) { toast(err.message, true); }
}

function renderImportPreview() {
  const box = $("#import-preview");
  const btn = $("#import-btn");
  if (!box) return;
  box.innerHTML = "";
  if (btn) btn.disabled = true;
  if (!state.backupFile) return;
  const devices = state.backupFile.devices || [];
  if (!devices.length) {
    box.innerHTML = "<p class='muted'>No devices in this file.</p>";
    return;
  }
  const list = document.createElement("div");
  list.className = "check-list";
  for (const d of devices) {
    const label = document.createElement("label");
    label.innerHTML = `<input type="checkbox" data-device-name="${esc(d.name)}" checked> ${esc(d.name)} <span class="muted">(${(d.readings || []).length})</span>`;
    list.appendChild(label);
  }
  box.appendChild(list);
  if (btn) btn.disabled = false;
}

async function doImport() {
  if (!state.backupFile) return;
  const checked = new Set([...document.querySelectorAll("#import-preview input:checked")].map((el) => el.dataset.deviceName));
  const devices = (state.backupFile.devices || []).filter((d) => checked.has(d.name));
  if (!devices.length) { toast("Select at least one device", true); return; }
  const resultsBox = $("#import-results");
  if (resultsBox) resultsBox.innerHTML = "<p class='muted'>Importing...</p>";
  try {
    const data = await api("/api/import", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ ...state.backupFile, devices }),
    });
    if (resultsBox) {
      resultsBox.innerHTML = "";
      for (const r of data.results) {
        const p = document.createElement("p");
        p.innerHTML = `<span class="badge ok">${r.new_readings} new</span> ${esc(r.name)} — ${r.new_readings} new, ${r.updated_readings} updated.`;
        resultsBox.appendChild(p);
      }
    }
    await refreshAll();
    toast(`Imported ${data.results.length} device(s)`);
  } catch (err) {
    if (resultsBox) resultsBox.innerHTML = "";
    toast(err.message, true);
  }
}

const _exportBtn = $("#export-btn");
if (_exportBtn) _exportBtn.addEventListener("click", doExport);
const _exportAll = $("#export-all");
if (_exportAll) _exportAll.addEventListener("click", () => $$("#export-list input").forEach((i) => { i.checked = true; }));
const _exportNone = $("#export-none");
if (_exportNone) _exportNone.addEventListener("click", () => $$("#export-list input").forEach((i) => { i.checked = false; }));
const _importFile = $("#import-file");
if (_importFile) _importFile.addEventListener("change", async () => {
  state.backupFile = null;
  renderImportPreview();
  const f = _importFile.files[0];
  if (!f) return;
  try {
    state.backupFile = JSON.parse(await f.text());
    if (!Array.isArray(state.backupFile.devices)) throw new Error("Not a Cyclewatch backup file");
    renderImportPreview();
  } catch (err) { toast(`Cannot read backup: ${err.message}`, true); }
});
const _importBtn = $("#import-btn");
if (_importBtn) _importBtn.addEventListener("click", doImport);

async function refreshAll() {
  await loadDevices();
  await Promise.all([loadDashboard(), loadData(), renderUploadHistory()]);
}

initTheme();
restoreDashFilters();
refreshAll().catch((err) => toast(err.message, true));
