// prashnam-voice frontend
//
// One-page app, two routes: project list (#/) and project editor (#/p/<id>).
// All state lives in the Python backend on disk; the browser is just an
// orchestrator. After any mutation we re-fetch the project and re-render.
//
// Auto-cascade on edit: changing a segment's English text triggers (after a
// 700 ms debounce) a regenerate-all on every selected language for that
// segment, which translates and re-synthesizes audio.

const $ = (sel, root = document) => root.querySelector(sel);
const $$ = (sel, root = document) => Array.from(root.querySelectorAll(sel));
const tpl = (id) => $(`#${id}`).content.firstElementChild.cloneNode(true);

const PACE_LABELS = {
  very_slow: "Very slow",
  slow: "Slow",
  moderate: "Moderate",
  fast: "Fast",
  very_fast: "Very fast",
};

const DEBOUNCE_MS = 5000;
const POLL_MS = 800;

const state = {
  langs: [],          // [{code, name, voice}]
  paces: [],
  defaultPace: "moderate",
  voicesByLang: {},   // {lang_code: [voice_id, ...]} — from active TTS adapter
  ivrKeys: { dtmf: ["1","2","3","4","5","6","7","8","9","0","*","#"], special: ["timeout","invalid"] },
  domains: [],        // [{name, label, description, segment_types, default_templates}]
  currentProject: null,
  // per-segment edit tracking
  // { lastSavedEnglish, lastRegenEnglish, editTimer, regenInFlight, jobId, surfaced: Set }
  segState: new Map(),
  // global poll timer (for /api/jobs); only runs while there's something active
  globalPollTimer: null,
  // jobs we know about: id -> {project_id, segment_id, langs}
  knownJobs: new Map(),
  // segment id to scroll into view + flash after the next render. Used by
  // the queue panel to deep-link into a specific segment.
  pendingScrollSegmentId: null,
};

function getSegState(sid) {
  let s = state.segState.get(sid);
  if (!s) {
    s = {
      lastSavedEnglish: null,
      lastRegenEnglish: null,
      editTimer: null,
      regenInFlight: false,
      jobId: null,
      // Lang codes whose audio we've already pulled into the cell during the
      // current job. Reset when a new job starts.
      surfacedAudio: new Set(),
      surfacedTranslation: false,
    };
    state.segState.set(sid, s);
  }
  return s;
}

function clearSegState(sid) {
  const s = state.segState.get(sid);
  if (s && s.editTimer) clearTimeout(s.editTimer);
  state.segState.delete(sid);
}

// --------------------------------------------------------------------------
// API helpers
// --------------------------------------------------------------------------

async function api(method, url, body) {
  const opts = { method, headers: {} };
  if (body !== undefined) {
    opts.headers["Content-Type"] = "application/json";
    opts.body = JSON.stringify(body);
  }
  const res = await fetch(url, opts);
  if (!res.ok) {
    let detail = `${res.status}`;
    try { detail = (await res.json()).detail || detail; } catch {}
    throw new Error(detail);
  }
  if (res.status === 204) return null;
  return res.json();
}

const Api = {
  langs:        ()                 => api("GET",   "/api/languages"),
  paces:        ()                 => api("GET",   "/api/paces"),
  domains:      ()                 => api("GET",   "/api/domains"),
  listProjects: ()                 => api("GET",   "/api/projects"),
  createProject:(name, langs, domain) => api("POST", "/api/projects", { name, langs, domain }),
  importCsv:    (file, domain, langs)  => importCsvUpload(file, domain, langs),
  getProject:   (pid)              => api("GET",   `/api/projects/${pid}`),
  updateProject:(pid, patch)       => api("PATCH", `/api/projects/${pid}`, patch),
  deleteProject:(pid)              => api("DELETE",`/api/projects/${pid}`),
  addSegment:   (pid, type, en)    => api("POST",  `/api/projects/${pid}/segments`, { type, english: en }),
  editSegment:  (pid, sid, english)=> api("PATCH", `/api/projects/${pid}/segments/${sid}`, { english }),
  deleteSegment:(pid, sid)         => api("DELETE",`/api/projects/${pid}/segments/${sid}`),
  regenerate:   (pid, sid, langs, rotationIds = null) =>
    api("POST", `/api/projects/${pid}/segments/${sid}/regenerate`,
        { langs, rotation_ids: rotationIds }),
  setUseTemplate: (pid, sid, use) => api("PATCH", `/api/projects/${pid}/segments/${sid}/template`, { use_template: use }),
  setOverride:    (pid, sid, body) => api("PATCH", `/api/projects/${pid}/segments/${sid}/override`, body),
  voices:         ()              => api("GET",   "/api/voices"),
  setLockAtEnd: (pid, sid, lock)   => api("PATCH", `/api/projects/${pid}/segments/${sid}/lock`, { lock_at_end: lock }),
  setEdge:      (pid, sid, k, t)   => api("PATCH", `/api/projects/${pid}/segments/${sid}/edge`, { key: k, target: t }),
  setPosition:  (pid, sid, x, y)   => api("PATCH", `/api/projects/${pid}/segments/${sid}/position`, { x, y }),
  setStartSegment: (pid, sid)      => api("PATCH", `/api/projects/${pid}/start-segment`, { segment_id: sid }),
  ivrKeys:      ()                 => api("GET",   "/api/ivr-keys"),
  enableRotations: (pid, body)     => api("POST",  `/api/projects/${pid}/rotations/enable`, body),
  disableRotations:(pid)           => api("POST",  `/api/projects/${pid}/rotations/disable`),
  reshuffle:    (pid, seed)        => api("POST",  `/api/projects/${pid}/rotations/reshuffle`, { seed: seed ?? null }),
  selectTake:   (pid, sid, l, aid, rid) => api("POST",  `/api/projects/${pid}/segments/${sid}/select`, { lang: l, attempt_id: aid, rotation_id: rid || "r0" }),
  listAttempts: (pid, sid, lang, rid) => api("GET",   `/api/projects/${pid}/segments/${sid}/attempts/${lang}` + (rid && rid !== "r0" ? `/${rid}` : "")),
  job:          (jobId)            => api("GET",   `/api/jobs/${jobId}`),
  jobs:         ()                 => api("GET",   "/api/jobs"),
  openFolder:   (pid)              => api("POST",  `/api/projects/${pid}/open-folder`),
};

function audioUrl(pid, sid, lang, attemptId, rotationId = "r0") {
  if (rotationId && rotationId !== "r0") {
    return `/api/projects/${pid}/audio/${sid}/${lang}/${rotationId}/${attemptId}.mp3`;
  }
  return `/api/projects/${pid}/audio/${sid}/${lang}/${attemptId}.mp3`;
}

function rotationLabel(rotationId) {
  const m = /^r(\d+)$/.exec(rotationId || "r0");
  return m ? `R${parseInt(m[1], 10) + 1}` : rotationId;
}

// --------------------------------------------------------------------------
// UI helpers — confirm dialog + snackbar (replace native confirm()/alert())
// --------------------------------------------------------------------------

function toast(message, kind = "info", duration = 2800) {
  const el = $("#snackbar");
  if (!el) return;
  el.className = "snackbar " + (kind === "info" ? "" : kind);
  el.textContent = message;
  el.hidden = false;
  // Force reflow so the .show transition kicks in.
  void el.offsetWidth;
  el.classList.add("show");
  if (toast._t) clearTimeout(toast._t);
  toast._t = setTimeout(() => {
    el.classList.remove("show");
    setTimeout(() => { if (!el.classList.contains("show")) el.hidden = true; }, 220);
  }, duration);
}

function confirmDialog({ title = "Confirm", body = "", okLabel = "Confirm", cancelLabel = "Cancel", danger = false } = {}) {
  const dlg = $("#confirm-dialog");
  if (!dlg) return Promise.resolve(window.confirm(body));
  $("#confirm-title").textContent = title;
  $("#confirm-body").textContent = body;
  const ok = $("#confirm-ok");
  const cancel = $("#confirm-cancel");
  ok.textContent = okLabel;
  cancel.textContent = cancelLabel;
  ok.classList.toggle("danger", !!danger);

  return new Promise((resolve) => {
    function done(v) {
      ok.removeEventListener("click", onOk);
      cancel.removeEventListener("click", onCancel);
      dlg.removeEventListener("close", onClose);
      dlg.removeEventListener("click", onBackdrop);
      if (dlg.open) dlg.close(v ? "ok" : "cancel");
      resolve(v);
    }
    function onOk()     { done(true); }
    function onCancel() { done(false); }
    function onClose()  { done(dlg.returnValue === "ok"); }
    function onBackdrop(ev) { if (ev.target === dlg) done(false); }

    ok.addEventListener("click", onOk);
    cancel.addEventListener("click", onCancel);
    dlg.addEventListener("close", onClose);
    dlg.addEventListener("click", onBackdrop);
    dlg.showModal();
    setTimeout(() => ok.focus(), 0);
  });
}

// Multipart upload for /api/projects/import — separate from `api()` because
// FormData and JSON don't mix on the same code path.
async function importCsvUpload(file, domain, langs) {
  const fd = new FormData();
  fd.append("file", file);
  fd.append("domain", domain || "poll");
  if (langs) fd.append("langs", langs);
  const res = await fetch("/api/projects/import", { method: "POST", body: fd });
  if (!res.ok) {
    let detail = `${res.status}`;
    try { detail = (await res.json()).detail || detail; } catch {}
    throw new Error(detail);
  }
  return res.json();
}

// Mirror of backend `effective_text(project, segment)`. Used to drive the live
// preview and the dirty/diff guard.
function optionIndex(proj, sid) {
  let n = 0;
  for (const s of proj.segments) {
    if (s.type === "option") {
      n += 1;
      if (s.id === sid) return n;
    }
  }
  return 0;
}

function effectiveText(proj, seg, body) {
  const text = (body ?? seg.english ?? "").trim();
  if (!text) return "";
  if (!seg.use_template) return text;
  if (seg.type === "question") {
    const tmpl = (proj.question_template || "").trim();
    if (!tmpl || !tmpl.includes("{body}")) return text;
    return tmpl.replace("{body}", text);
  }
  if (seg.type === "option") {
    const tmpl = (proj.option_template || "").trim();
    if (!tmpl || !tmpl.includes("{body}")) return text;
    const n = optionIndex(proj, seg.id);
    return tmpl.replace("{body}", text).replace("{n}", String(n));
  }
  return text;
}

// --------------------------------------------------------------------------
// Routing
// --------------------------------------------------------------------------

function parseRoute() {
  const h = location.hash || "#/";
  const m = h.match(/^#\/p\/([^/]+)\/?$/);
  if (m) return { name: "project", id: m[1] };
  return { name: "list" };
}

window.addEventListener("hashchange", render);

// --------------------------------------------------------------------------
// Render
// --------------------------------------------------------------------------

async function render() {
  const route = parseRoute();
  if (route.name === "project") {
    await renderProject(route.id);
  } else {
    await renderProjectList();
  }
  // After every render, see if the queue panel asked us to focus a segment.
  flushPendingScroll();
}

function flushPendingScroll() {
  const sid = state.pendingScrollSegmentId;
  if (!sid) return;
  const card = document.querySelector(`.seg[data-seg-id="${sid}"]`);
  if (!card) return;
  card.scrollIntoView({ behavior: "smooth", block: "center" });
  card.classList.remove("flash");
  // Re-trigger the keyframes animation.
  void card.offsetWidth;
  card.classList.add("flash");
  setTimeout(() => card.classList.remove("flash"), 1600);
  state.pendingScrollSegmentId = null;
}

function goToSegment(pid, sid) {
  if (!pid || !sid) return;
  state.pendingScrollSegmentId = sid;
  if (state.currentProject?.id === pid) {
    flushPendingScroll();
  } else {
    location.hash = `#/p/${encodeURIComponent(pid)}`;
  }
}

async function renderProjectList() {
  const root = $("#app");
  root.replaceChildren(tpl("tpl-project-list"));

  $("#new-project").addEventListener("click", onCreateProject);
  $("#import-csv").addEventListener("click", onImportCsv);

  let projects = [];
  try { projects = await Api.listProjects(); }
  catch (e) { console.error(e); }

  const ul = $("#projects");
  ul.innerHTML = "";
  if (!projects.length) { $("#projects-empty").hidden = false; return; }
  $("#projects-empty").hidden = true;

  for (const p of projects) {
    const li = document.createElement("li");
    li.innerHTML = `
      <a href="#/p/${encodeURIComponent(p.id)}">
        <span class="pname"></span>
        <span class="pmeta"></span>
      </a>
      <button class="ghost danger" data-del="${p.id}" title="Delete">✕</button>
    `;
    li.querySelector(".pname").textContent = p.name;
    li.querySelector(".pmeta").textContent =
      `${p.segment_count} segment${p.segment_count === 1 ? "" : "s"} · `
      + `${p.langs.length} lang${p.langs.length === 1 ? "" : "s"} · `
      + `${formatTime(p.updated_at)}`;
    li.querySelector("[data-del]").addEventListener("click", async (ev) => {
      ev.preventDefault();
      const ok = await confirmDialog({
        title: "Delete project?",
        body: `"${p.name}" will be removed along with all its audio takes. This can't be undone.`,
        okLabel: "Delete project",
        danger: true,
      });
      if (!ok) return;
      await Api.deleteProject(p.id);
      toast("Project deleted", "warn");
      renderProjectList();
    });
    ul.appendChild(li);
  }
}

async function renderProject(pid) {
  const root = $("#app");
  let proj;
  try { proj = await Api.getProject(pid); }
  catch (e) {
    root.innerHTML = `<section class="container"><p class="empty">Project not found.</p>
      <p><a href="#/">← back</a></p></section>`;
    return;
  }
  state.currentProject = proj;

  root.replaceChildren(tpl("tpl-project-editor"));

  $("#proj-name").textContent = proj.name;
  $("#proj-name").addEventListener("dblclick", () => makeNameEditable(proj));
  $("#proj-meta").textContent =
    `${proj.langs.length} languages · default pace ${PACE_LABELS[proj.default_pace] || proj.default_pace}`;

  $("#download-zip").href = `/api/projects/${proj.id}/zip`;
  $("#open-folder").addEventListener("click", () => Api.openFolder(proj.id));

  const enableBtn = $("#enable-rotations");
  if (enableBtn) {
    enableBtn.hidden = proj.rotation_count > 1;
    enableBtn.textContent = proj.rotation_count > 1
      ? `${proj.rotation_count} rotations active`
      : "Enable rotations";
    enableBtn.disabled = proj.rotation_count > 1;
    enableBtn.addEventListener("click", () => onEnableRotations(proj));
  }
  $("#delete-project").addEventListener("click", async () => {
    const ok = await confirmDialog({
      title: "Delete project?",
      body: `"${proj.name}" will be removed along with all its audio takes. This can't be undone.`,
      okLabel: "Delete project",
      danger: true,
    });
    if (!ok) return;
    await Api.deleteProject(proj.id);
    toast("Project deleted", "warn");
    location.hash = "#/";
  });

  // "Add" button targets whichever segment type this domain considers
  // repeatable (option for polls, body for announcements). Label adapts.
  const primary = primarySegmentType(proj.domain);
  const pack = domainPack(proj.domain);
  const primarySpec = pack?.segment_types.find((s) => s.name === primary);
  const addBtn = $("#add-option");
  if (addBtn && primarySpec) {
    addBtn.textContent = `+ Add ${primarySpec.label.toLowerCase()}`;
    addBtn.onclick = async () => {
      await Api.addSegment(proj.id, primary, "");
      await reloadProject();
    };
  }

  $("#save-settings").addEventListener("click", onSaveSettings);

  renderSettings(proj);
  renderSegments(proj);
  renderDag(proj);

  // If the project has no segments yet, auto-create the domain's seed
  // segment (question for polls, body for announcements). For IVR,
  // start with a prompt — users add menus / responses afterward.
  if (proj.segments.length === 0) {
    await Api.addSegment(proj.id, autoSeedSegmentType(proj.domain), "");
    await reloadProject();
  }
}

async function reloadProject() {
  if (!state.currentProject) return;
  const proj = await Api.getProject(state.currentProject.id);
  state.currentProject = proj;
  renderSegments(proj);
  renderDag(proj);
  $("#proj-meta").textContent =
    `${proj.langs.length} languages · default pace ${PACE_LABELS[proj.default_pace] || proj.default_pace}`;
  return proj;
}

// --------------------------------------------------------------------------
// Settings panel
// --------------------------------------------------------------------------

function renderSettings(proj) {
  $("#setting-name").value = proj.name;

  const paceSel = $("#setting-pace");
  paceSel.innerHTML = state.paces
    .map((p) => `<option value="${p}"${p === proj.default_pace ? " selected" : ""}>${PACE_LABELS[p] || p}</option>`)
    .join("");

  const langsBox = $("#setting-langs");
  langsBox.innerHTML = "";
  for (const l of state.langs) {
    const checked = proj.langs.includes(l.code);
    const sel = proj.paces[l.code] || "";
    const id = `setting-lang-${l.code}`;
    const row = document.createElement("div");
    row.className = "lang-row" + (checked ? "" : " disabled");
    row.innerHTML = `
      <input type="checkbox" id="${id}" value="${l.code}" ${checked ? "checked" : ""} />
      <label class="lang-label" for="${id}">${l.name} <span style="color:var(--muted)">(${l.code})</span></label>
      <select data-lang="${l.code}" ${checked ? "" : "disabled"}>
        <option value="">— default pace —</option>
        ${state.paces.map((p) => `<option value="${p}"${p === sel ? " selected" : ""}>${PACE_LABELS[p] || p}</option>`).join("")}
      </select>
    `;
    const cb = row.querySelector("input[type=checkbox]");
    const dropdown = row.querySelector("select");
    cb.addEventListener("change", () => {
      const on = cb.checked;
      row.classList.toggle("disabled", !on);
      dropdown.disabled = !on;
      if (!on) dropdown.value = "";
    });
    langsBox.appendChild(row);
  }

  $("#setting-question-tmpl").value = proj.question_template || "";
  $("#setting-option-tmpl").value = proj.option_template || "";

  renderRotationsFieldset(proj);

  // Lexicon — global textarea + a per-language textarea per project lang.
  const lex = proj.lexicon || {};
  $("#setting-lex-global").value = entriesToText(lex.global || {});
  const perLang = $("#setting-lex-perlang");
  perLang.innerHTML = "";
  for (const l of state.langs) {
    if (!proj.langs.includes(l.code)) continue;
    const lab = document.createElement("label");
    lab.className = "block";
    lab.innerHTML = `
      <span>${l.name} <span class="muted">(${l.code})</span></span>
      <textarea data-lang="${l.code}" rows="2" placeholder="(none)"></textarea>
    `;
    lab.querySelector("textarea").value = entriesToText(lex[l.code] || {});
    perLang.appendChild(lab);
  }
}

function entriesToText(obj) {
  const lines = [];
  for (const [k, v] of Object.entries(obj || {})) {
    lines.push(`${k}=${v}`);
  }
  return lines.join("\n");
}

function textToEntries(text) {
  const out = {};
  for (const raw of (text || "").split("\n")) {
    const line = raw.trim();
    if (!line || line.startsWith("#")) continue;
    const eq = line.indexOf("=");
    if (eq <= 0) continue;
    const k = line.slice(0, eq).trim();
    const v = line.slice(eq + 1).trim();
    if (k) out[k] = v;
  }
  return out;
}

async function onSaveSettings() {
  const proj = state.currentProject;
  if (!proj) return;
  const name = $("#setting-name").value.trim();
  const default_pace = $("#setting-pace").value;
  const langs = $$("#setting-langs input[type=checkbox]:checked").map((el) => el.value);
  const paces = {};
  for (const sel of $$("#setting-langs select")) {
    // Only honor pace overrides for languages that are still selected.
    if (sel.value && !sel.disabled) paces[sel.dataset.lang] = sel.value;
  }
  const question_template = $("#setting-question-tmpl").value;
  const option_template = $("#setting-option-tmpl").value;

  const lexicon = {};
  const globalLex = textToEntries($("#setting-lex-global").value);
  if (Object.keys(globalLex).length) lexicon.global = globalLex;
  for (const ta of $$("#setting-lex-perlang textarea")) {
    const entries = textToEntries(ta.value);
    if (Object.keys(entries).length) lexicon[ta.dataset.lang] = entries;
  }

  $("#settings-status").textContent = "saving…";
  try {
    await Api.updateProject(proj.id, {
      name, default_pace, langs, paces,
      question_template, option_template,
      lexicon,
    });
    $("#settings-status").textContent = "saved.";
    $("#proj-name").textContent = name;
    setTimeout(() => ($("#settings-status").textContent = ""), 2000);
    await reloadProject();
  } catch (e) {
    $("#settings-status").textContent = "error: " + e.message;
  }
}

// --------------------------------------------------------------------------
// Segments
// --------------------------------------------------------------------------

function renderSegments(proj) {
  const root = $("#segments");
  root.innerHTML = "";
  // Per-type counters so each card gets its own index label (Option 1,
  // Option 2, Body 1, Body 2, …).
  const counters = {};
  for (const seg of proj.segments) {
    counters[seg.type] = (counters[seg.type] || 0) + 1;
    root.appendChild(buildSegmentCard(proj, seg, counters[seg.type]));
  }
}

function buildSegmentCard(proj, seg, idx) {
  const card = tpl("tpl-segment-card");
  card.dataset.segId = seg.id;

  const pack = domainPack(proj.domain);
  const spec = pack?.segment_types.find((s) => s.name === seg.type);
  const typeLabel = spec ? spec.label : seg.type;

  const badge = $(".badge", card);
  // The "primary" type (question, body) gets the brand-colored badge;
  // repeatable types (option, body) get a numbered label.
  if (spec && spec.max === 1) {
    badge.textContent = typeLabel;
    badge.classList.add("q");
  } else {
    badge.textContent = `${typeLabel} ${idx}`;
  }

  const ta = $(".seg-en", card);
  ta.value = seg.english;
  ta.placeholder = seg.type === "question" ? "Type your poll question…" : "Type an option…";

  // Seed the edit state from what's already on disk. If the segment already
  // has a current take in any language, treat the current English as
  // "already regenerated" so we don't fire a regen on initial load.
  const segSt = getSegState(seg.id);
  segSt.lastSavedEnglish = seg.english;
  if (Object.keys(seg.current_takes || {}).length > 0) {
    segSt.lastRegenEnglish = seg.english;
  }

  ta.addEventListener("input", () => {
    updateEffectivePreview(card, proj, seg, ta.value);
    onSegmentEdit(proj.id, seg.id, ta.value, card);
  });

  // (reused below for ⟳ buttons that need empty-text feedback as a snackbar)

  // Preset pill: shown only if the relevant project template has {body}.
  const pill = $(".preset-pill", card);
  const hasTmpl = (seg.type === "question" && (proj.question_template || "").includes("{body}"))
    || (seg.type === "option" && (proj.option_template || "").includes("{body}"));
  if (hasTmpl) {
    pill.hidden = false;
    pill.classList.toggle("on", !!seg.use_template);
    pill.querySelector(".label").textContent = seg.use_template ? "Preset on" : "Preset off";
    pill.addEventListener("click", () => onTogglePreset(proj.id, seg.id, card));
  } else {
    pill.hidden = true;
  }

  // Lock-at-end pill: only visible on option segments while rotations
  // are active. Toggling re-shuffles non-canonical orderings.
  const lockPill = $(".lock-pill", card);
  if (lockPill && seg.type === "option" && proj.rotation_count > 1) {
    lockPill.hidden = false;
    lockPill.classList.toggle("on", !!seg.lock_at_end);
    lockPill.querySelector(".label").textContent = seg.lock_at_end ? "Locked at end" : "Lock at end";
    lockPill.addEventListener("click", () => onToggleLock(proj.id, seg.id));
  } else if (lockPill) {
    lockPill.hidden = true;
  }

  updateEffectivePreview(card, proj, seg, ta.value);

  $(".regen-row", card).addEventListener("click", () => {
    if (!ta.value.trim()) { toast("Type some English text first.", "warn"); return; }
    const proj = state.currentProject;
    const segNow = proj.segments.find((s) => s.id === seg.id);
    triggerRegen(proj.id, seg.id, proj.langs, effectiveText(proj, segNow, ta.value));
  });

  const delBtn = $(".del-seg", card);
  if (spec && !spec.deletable) {
    delBtn.disabled = true;
    delBtn.title = `Cannot delete the ${typeLabel.toLowerCase()} segment. Edit its text instead.`;
  } else {
    delBtn.addEventListener("click", async () => {
      const ok = await confirmDialog({
        title: `Delete this ${typeLabel.toLowerCase()}?`,
        body: "Audio takes for this segment stay on disk under audio/, but the segment itself will be removed from the project.",
        okLabel: "Delete",
        danger: true,
      });
      if (!ok) return;
      clearSegState(seg.id);
      await Api.deleteSegment(proj.id, seg.id);
      toast(`${typeLabel} deleted`, "warn");
      await reloadProject();
    });
  }

  const grid = $(".lang-grid", card);
  for (const code of proj.langs) {
    grid.appendChild(buildLangCell(proj, seg, code));
  }

  // Inline edges editor for IVR menu segments — keyboard-friendly fallback
  // to the DAG canvas. Each row is "<key>  →  [select target segment]".
  // Visible only on segments where outgoing edges make sense.
  if (proj.domain === "ivr" && _segmentCanHaveEdges(seg)) {
    card.appendChild(buildEdgesEditor(proj, seg));
  }

  // Stamp the type onto the badge so CSS can colour-code IVR segments.
  if (proj.domain === "ivr") badge.dataset.ivr = seg.type;

  setSegStatus(card, "");
  return card;
}

function _segmentCanHaveEdges(seg) {
  // Terminators end the call — no outgoing edges; bridges typically just
  // forward and aren't edited here.
  return seg.type === "menu" || seg.type === "prompt" || seg.type === "response";
}

function buildEdgesEditor(proj, seg) {
  const block = document.createElement("div");
  block.className = "edges-block";
  const title = seg.type === "menu" ? "DTMF edges" : "Next-on edges";
  block.innerHTML = `<h4>${title}</h4><div class="edges-grid"></div>`;
  const grid = block.querySelector(".edges-grid");
  // Menu segments offer the full DTMF + special set; prompt/response just
  // need a "next" edge — for v1 we surface the digits on prompt too in
  // case the prompt also handles DTMF.
  const keys = seg.type === "menu"
    ? [...state.ivrKeys.dtmf, ...state.ivrKeys.special]
    : [...state.ivrKeys.dtmf, ...state.ivrKeys.special];
  for (const key of keys) {
    const row = document.createElement("div");
    row.className = "edge-row";
    if ((seg.edges || {})[key]) row.classList.add("set");
    const sel = document.createElement("select");
    sel.dataset.key = key;
    sel.innerHTML = `<option value="">— unwired —</option>` +
      proj.segments
        .filter((s) => s.id !== seg.id)
        .map((s) => {
          const label = s.english.trim().slice(0, 38) || `(${s.type})`;
          const selected = (seg.edges || {})[key] === s.id ? " selected" : "";
          return `<option value="${s.id}"${selected}>${escapeHtml(label)} · ${s.type}</option>`;
        }).join("");
    sel.addEventListener("change", async () => {
      try {
        await Api.setEdge(proj.id, seg.id, key, sel.value || null);
        toast(sel.value ? `Edge ${key} set` : `Edge ${key} cleared`, "ok", 1400);
        await reloadProject();
      } catch (e) {
        toast("Edge save failed: " + e.message, "error");
      }
    });
    row.innerHTML = `<span class="edge-key">${key}</span>`;
    row.appendChild(sel);
    grid.appendChild(row);
  }
  return block;
}

function escapeHtml(s) {
  return (s || "").replace(/[&<>"']/g, (c) => ({
    "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;",
  })[c]);
}

function buildLangCell(proj, seg, lang) {
  const cell = tpl("tpl-lang-cell");
  cell.dataset.lang = lang;
  const meta = state.langs.find((l) => l.code === lang);
  $(".lang-name", cell).textContent = meta ? meta.name : lang;

  const tr = $(".translation", cell);
  tr.textContent = seg.translations[lang]?.r0 || "";

  const pill = $(".pill", cell);
  const player = $(".player", cell);
  const perLangTakes = seg.current_takes[lang] || {};
  const perLangTrans = seg.translations[lang] || {};
  const r0Att = perLangTakes.r0;
  // Decide which rotations this cell represents.
  // For question / body segments, only r0 matters even when rotations are on.
  const rotationsForCell = (seg.type === "option" && proj.rotation_count > 1)
    ? proj.rotation_ids || ["r0"]   // expected on payload; fall back below
    : ["r0"];
  const activeRotations = (proj.rotation_count > 1 && seg.type === "option")
    ? Array.from({ length: proj.rotation_count }, (_, i) => `r${i}`)
    : ["r0"];

  if (activeRotations.length === 1) {
    if (r0Att) {
      pill.textContent = "ready"; pill.classList.add("done");
      player.src = audioUrl(proj.id, seg.id, lang, r0Att);
      player.hidden = false;
      $(".takes", cell).hidden = false;
    } else if (perLangTrans.r0) {
      pill.textContent = "no audio yet"; pill.classList.add("stale");
    } else if (seg.english.trim()) {
      pill.textContent = "needs translation"; pill.classList.add("stale");
    } else {
      pill.textContent = "empty";
    }
  } else {
    // Multi-rotation: stack one row per rotation.
    player.hidden = true;
    const doneCount = activeRotations.filter((r) => perLangTakes[r]).length;
    if (doneCount === activeRotations.length) {
      pill.textContent = `ready · ${doneCount}/${activeRotations.length}`;
      pill.classList.add("done");
    } else if (doneCount > 0) {
      pill.textContent = `${doneCount}/${activeRotations.length}`;
      pill.classList.add("stale");
    } else if (Object.values(perLangTrans).length > 0) {
      pill.textContent = `0/${activeRotations.length} ready`;
      pill.classList.add("stale");
    } else if (seg.english.trim()) {
      pill.textContent = "needs translation"; pill.classList.add("stale");
    } else {
      pill.textContent = "empty";
    }
    const strip = document.createElement("div");
    strip.className = "rotation-strip";
    for (const rid of activeRotations) {
      const att = perLangTakes[rid];
      const row = document.createElement("div");
      row.className = "rotation-row";
      row.innerHTML = `
        <span class="rlabel">${rotationLabel(rid)}</span>
        ${att
          ? `<audio controls preload="none" src="${audioUrl(proj.id, seg.id, lang, att, rid)}"></audio>`
          : `<span class="muted small">— pending —</span>`}
        <button class="ghost rotation-regen" data-rotation="${rid}" title="Regenerate this rotation">⟳</button>
      `;
      strip.appendChild(row);
    }
    cell.querySelector(".rotation-strip")?.remove();
    // Insert after translation paragraph.
    cell.querySelector(".translation").after(strip);
    strip.querySelectorAll(".rotation-regen").forEach((btn) => {
      btn.addEventListener("click", () => {
        if (!seg.english.trim()) { toast("Type some English text first.", "warn"); return; }
        startJob(proj.id, seg.id, [lang], `cell:${lang}:${btn.dataset.rotation}`,
          { rotationIds: [btn.dataset.rotation] });
      });
    });
  }

  $(".regen-cell", cell).addEventListener("click", () => {
    if (!seg.english.trim()) { toast("Type some English text first.", "warn"); return; }
    startJob(proj.id, seg.id, [lang], `cell:${lang}`);
  });

  $(".takes", cell).addEventListener("toggle", async (ev) => {
    if (!ev.target.open) return;
    await loadTakes(proj.id, seg.id, lang, cell);
  });

  // Per-segment voice / pace overrides (option segments only — questions
  // and bodies inherit project-level voice/pace).
  const overrides = $(".overrides", cell);
  if (overrides && seg.type === "option") {
    overrides.hidden = false;
    populateOverrideRow(cell, proj, seg, lang);
  } else if (overrides) {
    overrides.hidden = true;
  }

  return cell;
}

function populateOverrideRow(cell, proj, seg, lang) {
  const voiceSel = cell.querySelector(".ovr-voice");
  const paceSel  = cell.querySelector(".ovr-pace");
  if (!voiceSel || !paceSel) return;

  const projectVoice = (proj.voices || {})[lang] || (state.langs.find((l) => l.code === lang) || {}).voice || "";
  const projectPace  = (proj.paces  || {})[lang] || proj.default_pace || "moderate";
  const segVoice = (seg.voices || {})[lang] || "";
  const segPace  = (seg.paces  || {})[lang] || "";
  const pool = state.voicesByLang[lang] || [projectVoice];

  // Voice select: first option is "(default — project's <name>)"; the rest
  // are the active-adapter voice pool. Selecting the default clears the
  // override.
  voiceSel.innerHTML = `<option value="">(default · ${projectVoice})</option>` +
    pool.map((v) => `<option value="${v}"${v === segVoice ? " selected" : ""}>${v}</option>`).join("");
  voiceSel.classList.toggle("set", !!segVoice);
  voiceSel.onchange = async () => {
    await applyOverride(proj.id, seg.id, lang, { voice: voiceSel.value || null }, voiceSel);
  };

  paceSel.innerHTML = `<option value="">(default · ${projectPace})</option>` +
    state.paces.map((p) => `<option value="${p}"${p === segPace ? " selected" : ""}>${PACE_LABELS[p] || p}</option>`).join("");
  paceSel.classList.toggle("set", !!segPace);
  paceSel.onchange = async () => {
    await applyOverride(proj.id, seg.id, lang, { pace: paceSel.value || null }, paceSel);
  };
}

async function applyOverride(pid, sid, lang, body, sourceEl) {
  try {
    const r = await Api.setOverride(pid, sid, { lang, ...body });
    // Reflect new state in the in-memory project so a re-render is consistent.
    const proj = state.currentProject;
    const segObj = proj?.segments.find((s) => s.id === sid);
    if (segObj) {
      Object.assign(segObj, r.segment);
    }
    sourceEl.classList.toggle("set", !!sourceEl.value);
    refreshSegmentCells(sid);
    toast(
      "voice" in body
        ? (body.voice ? `Voice for ${lang} → ${body.voice}` : `${lang} voice override cleared`)
        : (body.pace  ? `Pace for ${lang} → ${PACE_LABELS[body.pace] || body.pace}` : `${lang} pace override cleared`),
      "ok",
      1800,
    );
  } catch (e) {
    toast("Couldn't save override: " + e.message, "error");
  }
}

async function loadTakes(pid, sid, lang, cell) {
  const ul = $(".take-list", cell);
  ul.innerHTML = "<li><span class=\"muted\">loading…</span></li>";
  let attempts = [];
  try { attempts = (await Api.listAttempts(pid, sid, lang)).attempts; }
  catch (e) { ul.innerHTML = `<li class="muted">error: ${e.message}</li>`; return; }
  ul.innerHTML = "";
  const seg = state.currentProject.segments.find((s) => s.id === sid);
  const current = seg ? seg.current_takes[lang]?.r0 : null;
  if (!attempts.length) {
    ul.innerHTML = "<li class=\"muted\">no takes yet</li>"; return;
  }
  for (const a of attempts) {
    const li = document.createElement("li");
    if (a.id === current) li.classList.add("current");
    li.innerHTML = `
      <audio class="player" controls preload="none" src="${audioUrl(pid, sid, lang, a.id)}"></audio>
      <span class="muted">${a.pace} · ${formatTime(a.created_at)}</span>
      <button class="ghost set-current">${a.id === current ? "current" : "use this"}</button>
    `;
    li.querySelector(".set-current").addEventListener("click", async () => {
      await Api.selectTake(pid, sid, lang, a.id);
      await reloadProject();
    });
    ul.appendChild(li);
  }
}

// --------------------------------------------------------------------------
// Edit cascade — dirty-bit + diff-guarded retranslate + regenerate
// --------------------------------------------------------------------------

function onSegmentEdit(pid, sid, value, card) {
  const seg = getSegState(sid);
  setDirty(card, value !== (seg.lastSavedEnglish ?? ""));
  setSegStatus(card, value !== (seg.lastSavedEnglish ?? "") ? "editing…" : "");

  if (seg.editTimer) clearTimeout(seg.editTimer);
  seg.editTimer = setTimeout(async () => {
    seg.editTimer = null;
    await commitEdit(pid, sid, value, card);
  }, DEBOUNCE_MS);
}

async function commitEdit(pid, sid, value, card) {
  const seg = getSegState(sid);

  // Diff guard 1: nothing changed since last save → noop.
  if (value === seg.lastSavedEnglish) {
    setDirty(card, false);
    setSegStatus(card, "");
    return;
  }

  setSegStatus(card, "saving…");
  try {
    await Api.editSegment(pid, sid, value);
    seg.lastSavedEnglish = value;
    setDirty(card, false);
  } catch (e) {
    setSegStatus(card, "save error: " + e.message);
    return;
  }

  // Update the in-memory project so subsequent renders see the new english.
  const proj = state.currentProject;
  const segObj = proj?.segments.find((s) => s.id === sid);
  if (segObj) {
    segObj.english = value;
    segObj.translations = {};
    segObj.current_takes = {};
  }

  // Reflect the cleared translations / takes in the lang cells (without
  // touching the textarea).
  refreshSegmentCells(sid);

  // Diff guard 2: compare the effective text (template-aware), not just body.
  // Toggling the preset alone changes effective text → still triggers regen.
  const eff = segObj ? effectiveText(proj, segObj, value) : value;
  if (eff === seg.lastRegenEnglish) {
    setSegStatus(card, "saved · audio still current");
    return;
  }
  if (!eff.trim()) {
    setSegStatus(card, "saved · empty (no audio)");
    return;
  }

  triggerRegen(pid, sid, proj.langs, eff);
}

function triggerRegen(pid, sid, langs, sourceEnglish) {
  const seg = getSegState(sid);
  if (seg.regenInFlight) {
    // A regen is already running; the running one will finish first. The user
    // can click ⟳ again afterwards if they want a fresh take.
    setSegStatusById(sid, "regen already in flight…");
    return;
  }
  startJob(pid, sid, langs, sourceEnglish);
}

function setAutosave(text) {
  const el = $("#autosave-status");
  if (el) el.textContent = text || "";
}

function setDirty(card, dirty) {
  if (!card) return;
  const dot = card.querySelector(".dirty-dot");
  if (dot) dot.hidden = !dirty;
}

function setSegStatus(card, text) {
  if (!card) return;
  const el = card.querySelector(".seg-status");
  if (el) el.textContent = text || "";
}

function setSegStatusById(sid, text) {
  const card = document.querySelector(`.seg[data-seg-id="${sid}"]`);
  setSegStatus(card, text);
}

// --------------------------------------------------------------------------
// Surgical refresh of one segment's language cells.
// Does NOT touch the textarea — preserves whatever the user is typing.
// --------------------------------------------------------------------------

function refreshSegmentCells(sid) {
  const proj = state.currentProject;
  if (!proj) return;
  const seg = proj.segments.find((s) => s.id === sid);
  if (!seg) return;
  const card = document.querySelector(`.seg[data-seg-id="${sid}"]`);
  if (!card) return;
  const grid = card.querySelector(".lang-grid");
  if (!grid) return;
  grid.innerHTML = "";
  for (const code of proj.langs) {
    grid.appendChild(buildLangCell(proj, seg, code));
  }
}

// --------------------------------------------------------------------------
// Regenerate jobs — single global poller drives both per-cell UI and the
// right-side queue panel.
// --------------------------------------------------------------------------

async function startJob(pid, sid, langs, sourceEnglish, opts = {}) {
  const seg = getSegState(sid);
  if (seg.regenInFlight) return;          // guarded by triggerRegen too
  seg.regenInFlight = true;
  seg.surfacedAudio = new Set();
  seg.surfacedTranslation = false;
  seg.pendingSourceEnglish = sourceEnglish;

  for (const lang of langs) markCellPill(sid, lang, "busy", "queued");
  setSegStatusById(sid, "regenerating…");

  let resp;
  try {
    resp = await Api.regenerate(pid, sid, langs, opts.rotationIds || null);
  } catch (e) {
    seg.regenInFlight = false;
    for (const lang of langs) markCellPill(sid, lang, "error", e.message);
    setSegStatusById(sid, "regen error: " + e.message);
    return;
  }
  seg.jobId = resp.job_id;
  state.knownJobs.set(resp.job_id, { project_id: pid, segment_id: sid, langs });
  ensureGlobalPoller();
}

function ensureGlobalPoller() {
  if (state.globalPollTimer) return;
  const tick = async () => {
    let active;
    try { active = await Api.jobs(); }
    catch { return; }

    renderQueuePanel(active);

    const activeIds = new Set(active.map((j) => j.id));
    const seenJobs = new Set();
    for (const job of active) {
      seenJobs.add(job.id);
      await handleJobUpdate(job);
    }

    // Detect jobs we knew about that disappeared from the active list
    // (i.e. moved to done/error, or the server lost track of them after a
    // restart). For each, finalize so per-segment state (esp. regenInFlight)
    // gets cleared. If the per-job lookup itself 404s, synthesize an error
    // record so we still finalize — otherwise regenInFlight stays stuck and
    // future edits silently no-op with "regen already in flight".
    for (const [jobId, meta] of Array.from(state.knownJobs.entries())) {
      if (activeIds.has(jobId)) continue;
      let finalJob = null;
      try {
        finalJob = await Api.job(jobId);
      } catch {
        finalJob = {
          id: jobId,
          status: "error",
          error: "lost — server has no record of this job (likely restarted)",
          project_id: meta.project_id,
          segment_id: meta.segment_id,
          by_lang: {},
          elapsed_s: 0,
        };
      }
      await finalizeJob(finalJob);
      state.knownJobs.delete(jobId);
    }

    if (active.length === 0 && state.knownJobs.size === 0) {
      clearInterval(state.globalPollTimer);
      state.globalPollTimer = null;
    }
  };
  tick();
  state.globalPollTimer = setInterval(tick, POLL_MS);
}

async function handleJobUpdate(job) {
  // Only segment-regen jobs are interesting for the editor pane. Legacy
  // /api/generate jobs lack project_id and we just leave them in the queue
  // panel.
  if (!job.project_id || !job.segment_id) return;
  if (state.currentProject && job.project_id !== state.currentProject.id) return;

  const sid = job.segment_id;
  const seg = getSegState(sid);
  // Lock the segment as in-flight whenever the server tells us this job
  // is active. This is the only path that recovers state across page
  // reloads — without it, a reload mid-regen leaves regenInFlight=false
  // and a stray edit could fire a duplicate regen.
  seg.regenInFlight = true;
  seg.jobId = job.id;
  state.knownJobs.set(job.id, {
    project_id: job.project_id,
    segment_id: sid,
    langs: Object.keys(job.by_lang || {}),
  });

  // Update pills.
  for (const [lang, lp] of Object.entries(job.by_lang || {})) {
    if (lp.audio_done) markCellPill(sid, lang, "done", "ready");
    else if (lp.audio_started) markCellPill(sid, lang, "busy", "synthesizing…");
    else if (lp.translated) markCellPill(sid, lang, "busy", "queued");
    else markCellPill(sid, lang, "busy", job.status === "queued" ? "waiting" : "translating…");
  }

  // First time all langs have translations, surface the translation text.
  const allTranslated = Object.keys(job.by_lang || {}).length > 0
    && Object.values(job.by_lang).every((lp) => lp.translated);
  if (allTranslated && !seg.surfacedTranslation) {
    seg.surfacedTranslation = true;
    try {
      const proj = await Api.getProject(job.project_id);
      state.currentProject = proj;
      for (const lang of Object.keys(job.by_lang)) {
        updateCellTranslation(sid, lang, proj);
      }
    } catch {}
  }

  // For each lang that just became audio-ready, surface its player.
  const newlyDone = [];
  for (const [lang, lp] of Object.entries(job.by_lang || {})) {
    if (lp.audio_done && !seg.surfacedAudio.has(lang)) {
      seg.surfacedAudio.add(lang);
      newlyDone.push(lang);
    }
  }
  if (newlyDone.length) {
    try {
      const proj = await Api.getProject(job.project_id);
      state.currentProject = proj;
      for (const lang of newlyDone) updateCellAudio(sid, lang, proj);
    } catch {}
  }
}

async function finalizeJob(job) {
  if (!job.project_id || !job.segment_id) return;
  const sid = job.segment_id;
  const seg = getSegState(sid);
  seg.regenInFlight = false;
  seg.jobId = null;

  // Last refresh to reconcile any cell that didn't tick over while polling.
  try {
    if (state.currentProject && job.project_id === state.currentProject.id) {
      const proj = await Api.getProject(job.project_id);
      state.currentProject = proj;
      for (const lang of Object.keys(job.by_lang || {})) {
        updateCellTranslation(sid, lang, proj);
        updateCellAudio(sid, lang, proj);
      }
    }
  } catch {}

  if (job.status === "error") {
    setSegStatusById(sid, "regen error: " + (job.error || "unknown"));
  } else {
    if (seg.pendingSourceEnglish !== undefined) {
      seg.lastRegenEnglish = seg.pendingSourceEnglish;
    }
    setSegStatusById(sid, `regenerated · ${job.elapsed_s.toFixed(1)}s`);
    setTimeout(() => setSegStatusById(sid, ""), 4000);
  }
}

// --------------------------------------------------------------------------
// Preset pill + effective-text preview
// --------------------------------------------------------------------------

function updateEffectivePreview(card, proj, seg, body) {
  if (!card) return;
  const el = card.querySelector(".effective-preview");
  if (!el) return;
  const eff = effectiveText(proj, seg, body);
  // Only show the preview when wrapping actually changes the text.
  const bodyTrim = (body ?? "").trim();
  if (!seg.use_template || !eff || eff === bodyTrim) {
    el.hidden = true;
    el.textContent = "";
  } else {
    el.hidden = false;
    el.textContent = eff;
  }
}

async function onToggleLock(pid, sid) {
  const proj = state.currentProject;
  const segObj = proj?.segments.find((s) => s.id === sid);
  if (!segObj) return;
  const next = !segObj.lock_at_end;
  try {
    await Api.setLockAtEnd(pid, sid, next);
    toast(next ? "Pinned to last position" : "Lock removed", "ok", 1800);
    // Reshuffle re-derived rotations server-side; re-render to pick them up.
    await render();
  } catch (e) {
    toast("Couldn't toggle lock: " + e.message, "error");
  }
}

async function onTogglePreset(pid, sid, card) {
  const proj = state.currentProject;
  const segObj = proj?.segments.find((s) => s.id === sid);
  if (!segObj) return;
  const next = !segObj.use_template;
  const ta = card.querySelector(".seg-en");
  setSegStatus(card, next ? "preset on…" : "preset off…");
  try {
    const resp = await Api.setUseTemplate(pid, sid, next);
    Object.assign(segObj, resp.segment);
    // Pill state
    const pill = card.querySelector(".preset-pill");
    pill.classList.toggle("on", segObj.use_template);
    pill.querySelector(".label").textContent = segObj.use_template ? "Preset on" : "Preset off";
    // Cells now have empty translations + takes — refresh them.
    refreshSegmentCells(sid);
    updateEffectivePreview(card, proj, segObj, ta.value);
    setSegStatus(card, "");
    // If we have body text, kick off a regen so audio reflects the new wrapping.
    const eff = effectiveText(proj, segObj, ta.value);
    if (eff.trim()) {
      triggerRegen(pid, sid, proj.langs, eff);
    }
  } catch (e) {
    setSegStatus(card, "preset error: " + e.message);
  }
}

// --------------------------------------------------------------------------
// Surgical per-cell update helpers
// --------------------------------------------------------------------------

function findCell(sid, lang) {
  const card = document.querySelector(`.seg[data-seg-id="${sid}"]`);
  if (!card) return null;
  return card.querySelector(`.cell[data-lang="${lang}"]`);
}

function updateCellTranslation(sid, lang, proj) {
  const cell = findCell(sid, lang);
  if (!cell) return;
  const seg = proj.segments.find((s) => s.id === sid);
  if (!seg) return;
  const tr = cell.querySelector(".translation");
  if (tr) tr.textContent = seg.translations[lang]?.r0 || "";
}

function updateCellAudio(sid, lang, proj) {
  const cell = findCell(sid, lang);
  if (!cell) return;
  const seg = proj.segments.find((s) => s.id === sid);
  if (!seg) return;
  const att = seg.current_takes[lang]?.r0;
  if (!att) return;
  const player = cell.querySelector(".player");
  if (player) {
    const wasPlaying = !player.paused;
    if (player.dataset.attemptId !== att) {
      player.src = audioUrl(proj.id, sid, lang, att);
      player.dataset.attemptId = att;
    }
    player.hidden = false;
    if (wasPlaying) player.play().catch(() => {});
  }
  const takes = cell.querySelector(".takes");
  if (takes) takes.hidden = false;
}

// --------------------------------------------------------------------------
// Queue panel
// --------------------------------------------------------------------------

function renderQueuePanel(jobs) {
  const list = $("#queue-list");
  const count = $("#queue-count");
  if (!list || !count) return;

  if (jobs.length === 0) {
    count.hidden = true;
    list.innerHTML = "";
    return;
  }

  count.hidden = false;
  count.textContent = String(jobs.length);

  list.innerHTML = "";
  for (const job of jobs) {
    list.appendChild(buildQueueItem(job));
  }
}

function buildQueueItem(job) {
  const li = document.createElement("li");
  li.className = "queue-item";
  const where = describeJob(job);
  const elapsed = job.elapsed_s ? `${job.elapsed_s.toFixed(1)}s` : "";
  const langs = Object.entries(job.by_lang || {})
    .map(([lang, lp]) => {
      let cls = "qi-lang";
      let label = lang;
      if (lp.audio_done) { cls += " done"; label += " ✓"; }
      else if (lp.audio_started) { cls += " synth"; label += " ⏵"; }
      else if (lp.translated) { /* queued */ }
      else { cls += " translating"; label += " …"; }
      return `<span class="${cls}">${label}</span>`;
    })
    .join("");
  li.innerHTML = `
    <div class="qi-head">
      <span class="qi-status ${job.status}">${job.status}</span>
      <span class="qi-where"></span>
      <span class="qi-elapsed">${elapsed}</span>
    </div>
    <div class="qi-langs">${langs}</div>
  `;
  li.querySelector(".qi-where").textContent = where;

  if (job.project_id && job.segment_id) {
    li.classList.add("clickable");
    li.title = "Open this segment";
    li.addEventListener("click", () => goToSegment(job.project_id, job.segment_id));
  }
  return li;
}

function describeJob(job) {
  // If the job belongs to the open project, name the segment nicely.
  const proj = state.currentProject;
  if (proj && job.project_id === proj.id && job.segment_id) {
    let optIdx = 0;
    for (const s of proj.segments) {
      if (s.type === "option") optIdx += 1;
      if (s.id === job.segment_id) {
        return s.type === "question" ? `${proj.name} · Question` : `${proj.name} · Option ${optIdx}`;
      }
    }
  }
  if (job.project_id) return `${job.project_id} / ${job.segment_id || ""}`;
  if (job.run_id) return `legacy · ${job.run_id}`;
  return "(job)";
}

function setupQueuePanel() {
  const panel = $("#queue-panel");
  const handle = $("#queue-handle");
  const close = $("#queue-close");
  if (!panel || !handle || !close) return;
  handle.addEventListener("click", () => {
    panel.classList.toggle("open");
    panel.setAttribute("aria-hidden", panel.classList.contains("open") ? "false" : "true");
  });
  close.addEventListener("click", () => {
    panel.classList.remove("open");
    panel.setAttribute("aria-hidden", "true");
  });
}

function markCellPill(sid, lang, klass, text) {
  const seg = document.querySelector(`.seg[data-seg-id="${sid}"]`);
  if (!seg) return;
  const cell = seg.querySelector(`.cell[data-lang="${lang}"]`);
  if (!cell) return;
  const pill = cell.querySelector(".pill");
  pill.className = "pill " + klass;
  pill.textContent = text;
}

// --------------------------------------------------------------------------
// Project name inline edit
// --------------------------------------------------------------------------

function makeNameEditable(proj) {
  const el = $("#proj-name");
  el.contentEditable = "true";
  el.focus();
  const finish = async () => {
    el.contentEditable = "false";
    el.removeEventListener("blur", finish);
    const newName = el.textContent.trim() || proj.name;
    if (newName !== proj.name) {
      try {
        await Api.updateProject(proj.id, { name: newName });
        state.currentProject.name = newName;
      } catch (e) {
        el.textContent = proj.name;
        toast("Rename failed: " + e.message, "error");
      }
    }
  };
  el.addEventListener("blur", finish);
  el.addEventListener("keydown", (ev) => {
    if (ev.key === "Enter") { ev.preventDefault(); el.blur(); }
    if (ev.key === "Escape") { el.textContent = proj.name; el.blur(); }
  });
}

// --------------------------------------------------------------------------
// Project creation — uses a styled <dialog> instead of window.prompt.
// --------------------------------------------------------------------------

function openNewProjectDialog() {
  const dlg = $("#new-project-dialog");
  const input = $("#new-proj-name");
  if (!dlg || !input) return Promise.resolve(null);
  input.value = "";

  // Render domain options every open in case state.domains changed.
  const opts = $("#new-proj-domain-options");
  if (opts) {
    opts.innerHTML = "";
    state.domains.forEach((d, i) => {
      const id = `new-proj-domain-${d.name}`;
      const wrap = document.createElement("label");
      wrap.className = "engine-card";
      wrap.innerHTML = `
        <input type="radio" name="new-proj-domain" id="${id}" value="${d.name}" ${i === 0 ? "checked" : ""} />
        <div>
          <strong>${d.label}</strong>
          <p class="muted small">${d.description}</p>
        </div>
      `;
      opts.appendChild(wrap);
    });
  }

  return new Promise((resolve) => {
    function done() {
      dlg.removeEventListener("close", onClose);
      dlg.removeEventListener("click", onBackdropClick);
      const picked = document.querySelector('input[name="new-proj-domain"]:checked');
      const domain = picked ? picked.value : "poll";
      resolve(dlg.returnValue === "ok"
        ? { name: input.value.trim(), domain }
        : null);
    }
    function onClose() { done(); }
    function onBackdropClick(ev) {
      if (ev.target === dlg) dlg.close("cancel");
    }
    dlg.addEventListener("close", onClose, { once: true });
    dlg.addEventListener("click", onBackdropClick);
    dlg.showModal();
    setTimeout(() => input.focus(), 0);
  });
}

function setupNewProjectDialog() {
  const dlg = $("#new-project-dialog");
  const cancelBtn = $("#new-proj-cancel");
  if (cancelBtn) cancelBtn.addEventListener("click", () => dlg.close("cancel"));
}

async function onCreateProject() {
  const result = await openNewProjectDialog();
  if (!result || !result.name) return;
  const proj = await Api.createProject(result.name, null, result.domain);
  location.hash = `#/p/${encodeURIComponent(proj.id)}`;
}

// --------------------------------------------------------------------------
// CSV import
// --------------------------------------------------------------------------

function setupImportCsvDialog() {
  const dlg = $("#import-csv-dialog");
  const cancel = $("#import-csv-cancel");
  if (cancel) cancel.addEventListener("click", () => dlg.close("cancel"));
}

// --------------------------------------------------------------------------
// Rotations
// --------------------------------------------------------------------------

function setupEnableRotationsDialog() {
  const dlg = $("#enable-rotations-dialog");
  const cancel = $("#enable-rot-cancel");
  if (cancel) cancel.addEventListener("click", () => dlg.close("cancel"));
}

async function onEnableRotations(proj) {
  const dlg = $("#enable-rotations-dialog");
  if (!dlg) return;
  $("#enable-rot-count").value = "3";
  $("#enable-rot-nota").checked = true;

  const closed = new Promise((resolve) => {
    dlg.addEventListener("close", () => resolve(dlg.returnValue), { once: true });
  });
  dlg.showModal();
  const result = await closed;
  if (result !== "ok") return;

  const count = parseInt($("#enable-rot-count").value, 10);
  if (!Number.isFinite(count) || count < 2) return;
  const lock_last_as_nota = $("#enable-rot-nota").checked;

  setAutosave("enabling rotations…");
  try {
    await Api.enableRotations(proj.id, { count, lock_last_as_nota });
    toast(`Rotations enabled · ${count} orderings`, "ok");
    await reloadProject();
    // Re-render the editor so the toolbar button + per-cell stacks update.
    await render();
  } catch (e) {
    toast("Couldn't enable rotations: " + e.message, "error");
  }
}

function renderRotationsFieldset(proj) {
  const fs = $("#rotations-fieldset");
  if (!fs) return;
  if (!proj.rotation_count || proj.rotation_count <= 1) {
    fs.hidden = true;
    return;
  }
  fs.hidden = false;
  $("#setting-rotation-count").value = String(proj.rotation_count);
  $("#setting-rotation-seed").value = proj.rotation_seed != null ? String(proj.rotation_seed) : "";

  // Build the read-only orderings table.
  const tbody = fs.querySelector("tbody");
  tbody.innerHTML = "";
  const labelFor = (segId) => {
    const s = proj.segments.find((x) => x.id === segId);
    if (!s) return segId;
    return s.lock_at_end
      ? `${s.english || segId} <span class="locked-tag">🔒 last</span>`
      : (s.english || segId);
  };
  const rotations = proj.rotations || [];
  rotations.forEach((order, i) => {
    const tr = document.createElement("tr");
    const labels = order.map(labelFor).join(" · ");
    tr.innerHTML = `<td class="rcol-id">R${i + 1}</td><td class="rcol-order">${labels}</td>`;
    tbody.appendChild(tr);
  });

  $("#setting-reshuffle").onclick = async () => {
    const seedRaw = $("#setting-rotation-seed").value.trim();
    const seed = seedRaw ? parseInt(seedRaw, 10) : null;
    setAutosave("reshuffling…");
    try {
      await Api.reshuffle(proj.id, Number.isFinite(seed) ? seed : null);
      toast("Rotations reshuffled", "ok");
      await render();
    } catch (e) {
      toast("Reshuffle failed: " + e.message, "error");
    }
  };

  $("#setting-disable-rotations").onclick = async () => {
    const ok = await confirmDialog({
      title: "Disable rotations?",
      body: "All non-canonical (R2…) audio is left on disk but won't be regenerated. Disabling collapses back to a single ordering.",
      okLabel: "Disable",
      danger: true,
    });
    if (!ok) return;
    setAutosave("disabling rotations…");
    try {
      await Api.disableRotations(proj.id);
      toast("Rotations disabled", "warn");
      await render();
    } catch (e) {
      toast("Couldn't disable: " + e.message, "error");
    }
  };
}

async function refreshTopbarEngine() {
  const link = $("#topbar-engine");
  const label = $("#topbar-engine-name");
  if (!link || !label) return;
  try {
    const h = await api("GET", "/api/health");
    // The adapter for translator + tts is usually the same; show the
    // shared name when they match, otherwise show the translator's.
    const name = h.translator === h.tts ? h.translator : `${h.translator} / ${h.tts}`;
    label.textContent = name;
    link.classList.toggle("cloud", h.translator !== "local-ai4bharat" || h.tts !== "local-ai4bharat");
    link.title = `Active engine: ${name}. Click to switch.`;
  } catch {
    label.textContent = "engine ?";
  }
}

function setupHelpDialog() {
  const dlg = $("#help-dialog");
  const open = $("#help-btn");
  const closeBtn = $("#help-close-btn");
  if (!dlg || !open) return;
  open.addEventListener("click", () => dlg.showModal());
  if (closeBtn) closeBtn.addEventListener("click", () => dlg.close());
  // Click on the backdrop closes too.
  dlg.addEventListener("click", (ev) => {
    if (ev.target === dlg) dlg.close();
  });
}

async function onImportCsv() {
  const dlg = $("#import-csv-dialog");
  if (!dlg) return;
  const fileInput = $("#import-csv-file");
  const result = $("#import-csv-result");
  const submit = dlg.querySelector('button[type="submit"]');
  fileInput.value = "";
  result.hidden = true;
  result.className = "";
  result.textContent = "";

  // Make the submit button trigger the upload before letting the dialog close.
  // We do it via the form submit handler so Enter in the file input works too.
  const form = $("#import-csv-form");
  const handler = async (ev) => {
    if (!fileInput.files.length) return;          // browser will block via `required`
    ev.preventDefault();
    submit.disabled = true;
    result.hidden = false;
    result.className = "";
    result.textContent = "Importing…";

    const file = fileInput.files[0];
    const domain = (document.querySelector('input[name="import-domain"]:checked') || {}).value || "poll";

    try {
      const r = await Api.importCsv(file, domain, "");
      const created = r.created || [];
      const errors = r.errors || [];
      let html = `<strong>Created ${created.length} project(s)</strong> from ${r.rows_consumed} row(s).`;
      if (created.length) {
        html += "<ul>";
        for (const p of created) {
          html += `<li>${p.name} <span class="muted">— ${p.segments} segments</span></li>`;
        }
        html += "</ul>";
      }
      if (errors.length) {
        html += `<strong>${errors.length} error(s):</strong><ul>`;
        for (const e of errors) html += `<li>line ${e.line}: ${e.message}</li>`;
        html += "</ul>";
      }
      result.className = errors.length ? "warn" : "ok";
      result.innerHTML = html;
      // Re-render the list behind the dialog so new projects appear.
      renderProjectList();
    } catch (e) {
      result.className = "error";
      result.textContent = "Import failed: " + e.message;
    } finally {
      submit.disabled = false;
    }
  };
  form.addEventListener("submit", handler, { once: true });

  dlg.showModal();
}

function domainPack(name) {
  return state.domains.find((d) => d.name === name) || null;
}

function primarySegmentType(domain) {
  /* The "Add" button in the editor adds whichever segment type is the
     repeatable one for this domain. For poll → option, for announcement → body. */
  const pack = domainPack(domain);
  if (!pack) return "option";
  const addable = pack.segment_types.find((s) => s.addable);
  return addable ? addable.name : "option";
}

function autoSeedSegmentType(domain) {
  /* Type the editor auto-creates if the project has zero segments. For
     poll → "question" (max=1, not addable so user can't add another). For
     announcement → "body" (the first one). */
  const pack = domainPack(domain);
  if (!pack) return "question";
  const required = pack.segment_types.find((s) => s.max === 1);
  if (required) return required.name;
  return pack.segment_types[0]?.name || "body";
}

// --------------------------------------------------------------------------
// Utils
// --------------------------------------------------------------------------

function formatTime(iso) {
  if (!iso) return "—";
  try {
    const d = new Date(iso);
    return d.toLocaleString();
  } catch { return iso; }
}

// --------------------------------------------------------------------------
// IVR DAG canvas
// --------------------------------------------------------------------------
//
// Pure SVG. Each segment is a `<g class="dag-node">` containing a card
// rect, header tab, title, and one output port per edge key (menu types
// only). Edges are bezier paths between source-port and target-node-left.
//
// Three drag interactions:
//   1. Drag a node body → moves it. Persists `x/y` on mouseup.
//   2. Mousedown a port → rubber-band path follows cursor; mouseup over a
//      target node creates an edge for that port's key.
//   3. Click a node → flashes/scrolls to its segment card in the list view
//      (so the user can edit content).

const NODE_W = 200;
const NODE_H = 90;
const TAB_H = 22;
const PORT_R = 6;
const SVG_NS = "http://www.w3.org/2000/svg";

const dagState = {
  drag: null,         // { sid, dx, dy } when dragging a node
  connect: null,      // { fromSid, key, fromX, fromY } when wiring an edge
};

function renderDag(proj) {
  const pane = $("#dag-pane");
  if (!pane) return;
  if (proj.domain !== "ivr") {
    pane.hidden = true;
    return;
  }
  pane.hidden = false;

  // Bind toolbar (idempotent — replaceWith clones strip listeners).
  $("#dag-add-btn").onclick = onDagAddNode.bind(null, proj.id);
  $("#dag-walk-btn").onclick = openWalkSimulator;

  layoutSegmentsIfNeeded(proj);
  drawDag(proj);
}

function layoutSegmentsIfNeeded(proj) {
  // Projects loaded for the first time have x/y == 0 on every segment.
  // Cascade them on a grid so they're at least visible. Persist a
  // batch-update if anything changed. (No-op once positions are saved.)
  const needs = proj.segments.filter((s) => s.x === 0 && s.y === 0);
  if (needs.length < proj.segments.length) return;     // already laid out
  if (needs.length === 0) return;
  const cols = 3;
  const padX = 60, padY = 40;
  needs.forEach((s, i) => {
    s.x = padX + (i % cols) * (NODE_W + 60);
    s.y = padY + Math.floor(i / cols) * (NODE_H + 50);
  });
  // Fire and forget — store positions server-side so they survive reloads.
  for (const s of needs) {
    Api.setPosition(proj.id, s.id, s.x, s.y).catch(() => {});
  }
}

function drawDag(proj) {
  const svg = $("#dag-canvas");
  const nodesG = $("#dag-nodes");
  const edgesG = $("#dag-edges");
  if (!svg || !nodesG || !edgesG) return;
  nodesG.innerHTML = "";
  edgesG.innerHTML = "";

  const startId = (proj.start_segment_id || (proj.segments.find((s) => s.type === "prompt") || proj.segments[0] || {}).id);

  // Edges first so nodes paint on top.
  for (const seg of proj.segments) {
    const sourcePorts = portPositionsFor(seg);
    for (const [key, target] of Object.entries(seg.edges || {})) {
      const targetSeg = proj.segments.find((s) => s.id === target);
      if (!targetSeg) continue;
      const src = sourcePorts[key] || { x: seg.x + NODE_W, y: seg.y + NODE_H / 2 };
      const dst = { x: targetSeg.x, y: targetSeg.y + NODE_H / 2 };
      edgesG.appendChild(buildEdgePath(src, dst, key));
    }
  }

  for (const seg of proj.segments) {
    nodesG.appendChild(buildNode(seg, seg.id === startId));
  }

  // Wire mousemove + mouseup at the SVG level once.
  if (!svg.dataset.bound) {
    svg.addEventListener("mousemove", onDagMouseMove);
    svg.addEventListener("mouseup",   onDagMouseUp);
    svg.addEventListener("mouseleave",onDagMouseUp);
    svg.dataset.bound = "1";
  }
}

function portKeysFor(seg) {
  if (seg.type === "menu") {
    return [...state.ivrKeys.dtmf, ...state.ivrKeys.special];
  }
  if (seg.type === "prompt" || seg.type === "response" || seg.type === "bridge") {
    // One "next" port keyed on "1" by convention so the user can wire
    // a default forward path. Plus the special keys for completeness.
    return ["1", ...state.ivrKeys.special];
  }
  return [];
}

function portPositionsFor(seg) {
  const out = {};
  const keys = portKeysFor(seg);
  if (!keys.length) return out;
  // Spread ports along the right edge of the card.
  const top = seg.y + TAB_H + 8;
  const bottom = seg.y + NODE_H - 8;
  const span = Math.max(0, bottom - top);
  keys.forEach((k, i) => {
    const t = keys.length === 1 ? 0.5 : i / (keys.length - 1);
    out[k] = { x: seg.x + NODE_W, y: top + t * span };
  });
  return out;
}

function buildNode(seg, isStart) {
  const g = document.createElementNS(SVG_NS, "g");
  g.classList.add("dag-node");
  if (isStart) g.classList.add("start");
  g.dataset.sid = seg.id;
  g.dataset.type = seg.type;
  g.setAttribute("transform", `translate(${seg.x}, ${seg.y})`);

  const rect = document.createElementNS(SVG_NS, "rect");
  rect.setAttribute("class", "dag-card");
  rect.setAttribute("width", NODE_W);
  rect.setAttribute("height", NODE_H);
  g.appendChild(rect);

  const tab = document.createElementNS(SVG_NS, "rect");
  tab.setAttribute("class", "dag-card-tab");
  tab.setAttribute("width", NODE_W);
  tab.setAttribute("height", TAB_H);
  tab.setAttribute("rx", 8); tab.setAttribute("ry", 8);
  g.appendChild(tab);
  // Square off the bottom of the tab — overlay a strip with no rounding.
  const tabClip = document.createElementNS(SVG_NS, "rect");
  tabClip.setAttribute("class", "dag-card-tab");
  tabClip.setAttribute("y", TAB_H - 8);
  tabClip.setAttribute("width", NODE_W);
  tabClip.setAttribute("height", 8);
  g.appendChild(tabClip);

  const tabText = document.createElementNS(SVG_NS, "text");
  tabText.setAttribute("class", "dag-tab-label");
  tabText.setAttribute("x", 10);
  tabText.setAttribute("y", 14);
  tabText.textContent = (isStart ? "★ " : "") + seg.type;
  g.appendChild(tabText);

  const title = document.createElementNS(SVG_NS, "text");
  title.setAttribute("class", "dag-title");
  title.setAttribute("x", 10);
  title.setAttribute("y", TAB_H + 22);
  title.textContent = (seg.english.trim().slice(0, 28)) || `(${seg.type})`;
  g.appendChild(title);

  const snippet = document.createElementNS(SVG_NS, "text");
  snippet.setAttribute("class", "dag-snippet");
  snippet.setAttribute("x", 10);
  snippet.setAttribute("y", TAB_H + 40);
  const tail = seg.english.trim().slice(28, 64);
  snippet.textContent = tail || "";
  g.appendChild(snippet);

  // Ports on the right edge.
  const positions = portPositionsFor(seg);
  Object.entries(positions).forEach(([key, pos]) => {
    const cx = pos.x - seg.x;
    const cy = pos.y - seg.y;
    const port = document.createElementNS(SVG_NS, "circle");
    port.setAttribute("class", "dag-port" + ((seg.edges || {})[key] ? " connected" : ""));
    port.setAttribute("cx", cx);
    port.setAttribute("cy", cy);
    port.setAttribute("r", PORT_R);
    port.dataset.key = key;
    port.addEventListener("mousedown", (ev) => onPortMouseDown(ev, seg, key));
    g.appendChild(port);

    const lab = document.createElementNS(SVG_NS, "text");
    lab.setAttribute("class", "dag-port-label");
    lab.setAttribute("x", cx + 9);
    lab.setAttribute("y", cy + 3);
    lab.textContent = key;
    g.appendChild(lab);
  });

  // Drag-to-move on the card body (not the ports).
  rect.addEventListener("mousedown", (ev) => onNodeMouseDown(ev, seg));
  tab.addEventListener("mousedown",  (ev) => onNodeMouseDown(ev, seg));
  // Click to focus the matching segment card in the list below.
  g.addEventListener("dblclick", () => {
    state.pendingScrollSegmentId = seg.id;
    flushPendingScroll();
  });
  return g;
}

function buildEdgePath(src, dst, key) {
  const p = document.createElementNS(SVG_NS, "path");
  const isSpecial = state.ivrKeys.special.includes(key);
  p.setAttribute("class", "dag-edge" + (isSpecial ? " special" : ""));
  p.setAttribute("d", bezier(src, dst));
  const g = document.createElementNS(SVG_NS, "g");
  g.appendChild(p);
  // Label on midpoint.
  const mid = { x: (src.x + dst.x) / 2, y: (src.y + dst.y) / 2 - 4 };
  const lab = document.createElementNS(SVG_NS, "text");
  lab.setAttribute("class", "dag-edge-label" + (isSpecial ? " special" : ""));
  lab.setAttribute("x", mid.x);
  lab.setAttribute("y", mid.y);
  lab.setAttribute("text-anchor", "middle");
  lab.textContent = key;
  g.appendChild(lab);
  return g;
}

function bezier(src, dst) {
  // Horizontal bezier: source goes right, destination comes from the left.
  const dx = Math.max(40, Math.abs(dst.x - src.x) * 0.5);
  return `M ${src.x} ${src.y} C ${src.x + dx} ${src.y}, ${dst.x - dx} ${dst.y}, ${dst.x} ${dst.y}`;
}

// --------------------------------------------------------------------------
// DAG drag interactions
// --------------------------------------------------------------------------

function _svgPoint(ev) {
  const svg = $("#dag-canvas");
  const r = svg.getBoundingClientRect();
  // SVG viewBox = native pixel space (we set width/height in px). r maps
  // 1:1, accounting for scroll on the wrapper.
  const wrap = svg.parentElement;
  return {
    x: ev.clientX - r.left + wrap.scrollLeft,
    y: ev.clientY - r.top  + wrap.scrollTop,
  };
}

function onNodeMouseDown(ev, seg) {
  if (ev.button !== 0) return;
  ev.preventDefault();
  ev.stopPropagation();
  const pt = _svgPoint(ev);
  dagState.drag = { sid: seg.id, dx: pt.x - seg.x, dy: pt.y - seg.y };
  const g = ev.currentTarget.closest(".dag-node");
  if (g) g.classList.add("dragging");
}

function onPortMouseDown(ev, seg, key) {
  if (ev.button !== 0) return;
  ev.preventDefault();
  ev.stopPropagation();
  const pos = portPositionsFor(seg)[key];
  dagState.connect = { fromSid: seg.id, key, fromX: pos.x, fromY: pos.y };
  const rubber = $("#dag-rubber");
  rubber.hidden = false;
  rubber.setAttribute("d", bezier({ x: pos.x, y: pos.y }, { x: pos.x + 1, y: pos.y }));
}

function onDagMouseMove(ev) {
  if (dagState.drag) {
    const proj = state.currentProject;
    const seg = proj?.segments.find((s) => s.id === dagState.drag.sid);
    if (!seg) return;
    const pt = _svgPoint(ev);
    seg.x = Math.max(0, pt.x - dagState.drag.dx);
    seg.y = Math.max(0, pt.y - dagState.drag.dy);
    drawDag(proj);
  }
  if (dagState.connect) {
    const pt = _svgPoint(ev);
    const rubber = $("#dag-rubber");
    rubber.setAttribute("d", bezier(
      { x: dagState.connect.fromX, y: dagState.connect.fromY },
      { x: pt.x, y: pt.y },
    ));
  }
}

async function onDagMouseUp(ev) {
  // Finishing a node-drag → persist the position.
  if (dagState.drag) {
    const proj = state.currentProject;
    const seg = proj?.segments.find((s) => s.id === dagState.drag.sid);
    document.querySelectorAll(".dag-node.dragging").forEach((g) => g.classList.remove("dragging"));
    if (seg) {
      try { await Api.setPosition(proj.id, seg.id, seg.x, seg.y); }
      catch {}
    }
    dagState.drag = null;
  }

  // Finishing a port-drag → did we land on a node?
  if (dagState.connect) {
    const rubber = $("#dag-rubber");
    rubber.hidden = true;
    const target = ev.target.closest(".dag-node");
    const proj = state.currentProject;
    if (target && proj) {
      const targetSid = target.dataset.sid;
      const { fromSid, key } = dagState.connect;
      if (targetSid !== fromSid) {
        try {
          await Api.setEdge(proj.id, fromSid, key, targetSid);
          toast(`Wired ${key} → ${target.querySelector(".dag-title")?.textContent || targetSid}`, "ok", 1600);
          await reloadProject();
        } catch (e) {
          toast("Couldn't wire edge: " + e.message, "error");
        }
      }
    }
    dagState.connect = null;
  }
}

async function onDagAddNode(pid) {
  const sel = $("#dag-add-type");
  const type = sel.value || "prompt";
  try {
    const r = await Api.addSegment(pid, type, "");
    // Scatter near the centre.
    await Api.setPosition(pid, r.segment_id, 80 + Math.random() * 80, 80 + Math.random() * 80);
    await reloadProject();
  } catch (e) {
    toast("Add failed: " + e.message, "error");
  }
}

// --------------------------------------------------------------------------
// Walk simulator
// --------------------------------------------------------------------------

const walkState = {
  current: null,    // segment id
  history: [],      // [seg_id, ...] in visit order
  lang: "en",
  rotationId: "r0",
};

function openWalkSimulator() {
  const proj = state.currentProject;
  if (!proj) return;
  const dlg = $("#walk-dialog");
  if (!dlg) return;

  // Populate language + rotation selects.
  const langSel = $("#walk-lang");
  langSel.innerHTML = proj.langs.map((c) => {
    const meta = state.langs.find((l) => l.code === c) || { name: c };
    return `<option value="${c}">${escapeHtml(meta.name)}</option>`;
  }).join("");
  langSel.value = proj.langs.includes("en") ? "en" : proj.langs[0];

  const rotSel = $("#walk-rotation");
  const rids = proj.rotation_count > 1
    ? Array.from({ length: proj.rotation_count }, (_, i) => `r${i}`)
    : ["r0"];
  rotSel.innerHTML = rids.map((r) => `<option value="${r}">${rotationLabel(r)}</option>`).join("");
  rotSel.value = "r0";

  walkState.lang = langSel.value;
  walkState.rotationId = rotSel.value;

  // Resolve start.
  const start = proj.start_segment_id ||
    (proj.segments.find((s) => s.type === "prompt") || proj.segments[0] || {}).id;
  walkState.current = start || null;
  walkState.history = start ? [start] : [];

  bindWalkOnce();
  drawWalk();
  dlg.showModal();
}

let _walkBound = false;
function bindWalkOnce() {
  if (_walkBound) return;
  _walkBound = true;
  $("#walk-close").addEventListener("click", () => $("#walk-dialog").close());
  $("#walk-close-2").addEventListener("click", () => $("#walk-dialog").close());
  $("#walk-reset").addEventListener("click", () => {
    const proj = state.currentProject;
    const start = proj?.start_segment_id ||
      (proj?.segments.find((s) => s.type === "prompt") || proj?.segments[0] || {}).id;
    walkState.current = start || null;
    walkState.history = start ? [start] : [];
    drawWalk();
  });
  $("#walk-lang").addEventListener("change", (ev) => {
    walkState.lang = ev.target.value;
    drawWalk();
  });
  $("#walk-rotation").addEventListener("change", (ev) => {
    walkState.rotationId = ev.target.value;
    drawWalk();
  });
}

function drawWalk() {
  const proj = state.currentProject;
  if (!proj) return;
  const seg = proj.segments.find((s) => s.id === walkState.current);
  const tag = $("#walk-tag");
  const title = $("#walk-title");
  const text = $("#walk-text");
  const audio = $("#walk-audio");
  const hint = $("#walk-hint");
  const keypad = $("#walk-keypad");
  const trail = $("#walk-history");

  if (!seg) {
    tag.textContent = "—";
    title.textContent = "(no start segment)";
    text.textContent = "Add at least one segment, then set it as the start.";
    audio.removeAttribute("src");
    keypad.innerHTML = "";
    trail.innerHTML = "";
    hint.textContent = "";
    return;
  }

  tag.textContent = seg.type.toUpperCase();
  title.textContent = (seg.english.trim() || `(${seg.type})`);
  const tr = (seg.translations[walkState.lang] || {})[walkState.rotationId] || "";
  text.textContent = tr || "(no translation yet — regenerate this segment to hear it)";
  const att = (seg.current_takes[walkState.lang] || {})[walkState.rotationId];
  if (att) {
    audio.src = audioUrl(proj.id, seg.id, walkState.lang, att, walkState.rotationId);
    audio.play().catch(() => {});
    hint.textContent = "Auto-playing. Press a DTMF key to follow that edge.";
  } else {
    audio.removeAttribute("src");
    hint.textContent = "(audio not generated yet — regenerate this segment.)";
  }

  // Keypad: 1-9, *, 0, # plus special keys.
  keypad.innerHTML = "";
  const layout = ["1","2","3","4","5","6","7","8","9","*","0","#"];
  for (const k of layout) {
    const b = document.createElement("button");
    b.type = "button";
    b.textContent = k;
    const wired = !!(seg.edges || {})[k];
    if (!wired) b.classList.add("unwired");
    b.addEventListener("click", () => walkPress(k));
    keypad.appendChild(b);
  }
  for (const k of state.ivrKeys.special) {
    const b = document.createElement("button");
    b.type = "button";
    b.className = "special";
    b.textContent = k;
    if (!(seg.edges || {})[k]) b.classList.add("unwired");
    b.addEventListener("click", () => walkPress(k));
    keypad.appendChild(b);
  }

  // History breadcrumb.
  trail.innerHTML = walkState.history.map((sid, i) => {
    const s = proj.segments.find((x) => x.id === sid);
    const label = s ? (s.english.trim().slice(0, 24) || s.type) : sid;
    const isCurrent = i === walkState.history.length - 1;
    return `<li class="${isCurrent ? "current" : ""}">${escapeHtml(label)}</li>`;
  }).join("");
}

function walkPress(key) {
  const proj = state.currentProject;
  const seg = proj?.segments.find((s) => s.id === walkState.current);
  if (!seg) return;
  const next = (seg.edges || {})[key];
  if (!next) {
    toast(`No edge wired for ${key}`, "warn", 1600);
    return;
  }
  walkState.current = next;
  walkState.history.push(next);
  drawWalk();
}


// --------------------------------------------------------------------------
// Bootstrap
// --------------------------------------------------------------------------

(async () => {
  try {
    const [langs, paces, domains, voices, ivrKeys] = await Promise.all([
      Api.langs(), Api.paces(), Api.domains(), Api.voices(), Api.ivrKeys().catch(() => null),
    ]);
    state.langs = langs;
    state.paces = paces.options;
    state.defaultPace = paces.default;
    state.domains = domains;
    state.voicesByLang = voices;
    if (ivrKeys) state.ivrKeys = ivrKeys;
  } catch (e) {
    document.getElementById("app").innerHTML =
      `<section class="container"><p>Failed to reach the API: ${e.message}</p></section>`;
    return;
  }
  setupQueuePanel();
  setupNewProjectDialog();
  setupImportCsvDialog();
  setupHelpDialog();
  setupEnableRotationsDialog();
  refreshTopbarEngine();
  await render();
  // Pick up any jobs already running on the server (e.g. after a page reload
  // mid-regen). The poller self-stops when nothing is active.
  ensureGlobalPoller();
})();
