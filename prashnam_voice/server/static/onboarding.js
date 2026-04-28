// Onboarding wizard. Two parallel paths share the rail/stage layout:
//
//   local  → pick → download → done
//   sarvam → pick → sarvam-account → sarvam-key (test) → done
//
// The local flow used to detour through "make an HF account → accept ToS on
// two gated repos → paste a token", because the original AI4Bharat repos
// were gated. We now mirror them publicly under naklitechie/* (MIT +
// Apache-2.0 both permit redistribution), so the local flow is just engine
// pick → background model download → done. No tokens.
//
// State is held in module scope; on completion we POST /api/onboarding/complete
// to persist the choice + settings, then redirect to /.

const $ = (sel, root = document) => root.querySelector(sel);

// Clone the whole template content (a DocumentFragment), not just the first
// element child. Each onboarding step template has multiple siblings at the
// top level — heading + paragraph + actions row — so grabbing only the
// first would (and did) silently drop the buttons.
const tpl = (id) => document.getElementById(id).content.cloneNode(true);

const FLOWS = {
  local: [
    { id: "pick",        label: "Engine"   },
    { id: "download",    label: "Download" },
    { id: "done",        label: "Done"     },
  ],
  sarvam: [
    { id: "pick",            label: "Engine"   },
    { id: "sarvam-account",  label: "Sign up"  },
    { id: "sarvam-key",      label: "API key"  },
    { id: "done",            label: "Done"     },
  ],
};

const state = {
  engine: "local",   // current engine selection
  step: "pick",      // current step id
  sarvamKey: "",
};

// --------------------------------------------------------------------------
// API
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
  return res.json();
}

const Api = {
  testSarvam:(api_key) => api("POST", "/api/onboarding/test-sarvam", { api_key }),
  complete:  (payload) => api("POST", "/api/onboarding/complete", payload),
  startDownload: () => api("POST", "/api/onboarding/download-models", { token: null }),
  downloadProgress: () => api("GET", "/api/onboarding/download-progress"),
};

// --------------------------------------------------------------------------
// Rail
// --------------------------------------------------------------------------

function renderRail() {
  const rail = $("#rail-steps");
  rail.innerHTML = "";
  const flow = FLOWS[state.engine];
  const idx = flow.findIndex((s) => s.id === state.step);
  flow.forEach((s, i) => {
    const li = document.createElement("li");
    li.textContent = s.label;
    if (i < idx) li.classList.add("done");
    if (i === idx) li.classList.add("current");
    rail.appendChild(li);
  });
}

// --------------------------------------------------------------------------
// Stage rendering
// --------------------------------------------------------------------------

function go(step) {
  state.step = step;
  renderRail();

  const stage = $("#stage");
  stage.innerHTML = "";
  const node = stepNode(step);
  if (node) stage.appendChild(node);

  bindStep(step);
}

function stepNode(step) {
  if (step === "pick")           return tpl("step-pick-engine");
  if (step === "download")       return tpl("step-download");
  if (step === "sarvam-account") return tpl("step-sarvam-account");
  if (step === "sarvam-key")     return tpl("step-sarvam-key");
  if (step === "done")           return tpl("step-done");
  return null;
}

function bindStep(step) {
  // Generic "Back" / "Next" buttons that just step through the flow.
  document.querySelectorAll("[data-back]").forEach((b) =>
    b.addEventListener("click", () => goPrev()));
  document.querySelectorAll("[data-next]").forEach((b) => {
    if (b.id === "btn-sarvam-next") return;  // handled below
    b.addEventListener("click", () => goNext());
  });

  if (step === "pick") {
    $("#btn-pick-next").addEventListener("click", () => {
      const picked = document.querySelector('input[name="engine"]:checked');
      state.engine = picked ? picked.value : "local";
      // For local: persist config now (no further input needed) and move
      // straight to the download step. For sarvam: just advance — the
      // sarvam-key step calls complete() once the user pastes a key.
      if (state.engine === "local") {
        complete().then(() => goNext()).catch(() => {});
      } else {
        goNext();
      }
    });
  }

  if (step === "download") {
    bindDownloadStep();
  }

  if (step === "sarvam-key") {
    const input = $("#sarvam-key");
    if (state.sarvamKey) input.value = state.sarvamKey;
    $("#btn-test-sarvam").addEventListener("click", onTestSarvam);
    $("#btn-sarvam-next").addEventListener("click", () => {
      state.sarvamKey = input.value;
      complete().then(() => goNext());
    });
  }

  if (step === "done") {
    const detail = $("#done-detail");
    if (detail) {
      detail.textContent = state.engine === "local"
        ? "Local engines configured. Models download on first regeneration (~4.5 GB, one-time)."
        : "Sarvam configured. You're ready to generate audio.";
    }
  }
}

function goNext() {
  const flow = FLOWS[state.engine];
  const i = flow.findIndex((s) => s.id === state.step);
  if (i < flow.length - 1) go(flow[i + 1].id);
}
function goPrev() {
  const flow = FLOWS[state.engine];
  const i = flow.findIndex((s) => s.id === state.step);
  if (i > 0) go(flow[i - 1].id);
}

// --------------------------------------------------------------------------
// Model download step
// --------------------------------------------------------------------------

let _downloadPollTimer = null;

function bindDownloadStep() {
  const skipBtn = $("#btn-skip-download");
  const nextBtn = $("#btn-download-next");
  if (skipBtn) skipBtn.addEventListener("click", () => {
    stopDownloadPoll();
    goNext();
  });
  // Kick off the actual download. The endpoint is a no-op if a job's
  // already running, so this is safe to call on every entry into the step.
  Api.startDownload().catch(() => { /* server-side error surfaces via progress poll */ });
  // Poll once immediately, then on an interval.
  pollDownload();
  _downloadPollTimer = setInterval(pollDownload, 1000);
}

function stopDownloadPoll() {
  if (_downloadPollTimer) {
    clearInterval(_downloadPollTimer);
    _downloadPollTimer = null;
  }
}

async function pollDownload() {
  let job;
  try { job = await Api.downloadProgress(); }
  catch { return; }

  const errEl = $("#download-error");
  if (job.error) {
    errEl.hidden = false;
    errEl.textContent = job.error;
  } else if (errEl) {
    errEl.hidden = true;
  }

  let allDone = true;
  for (const [modelId, mp] of Object.entries(job.models || {})) {
    const card = document.querySelector(`.model-progress[data-model="${modelId}"]`);
    if (!card) continue;
    const bar = card.querySelector("progress");
    const status = card.querySelector(".status-text");
    const bytes = card.querySelector(".bytes");
    const total = mp.total_bytes || 0;
    const done = mp.downloaded_bytes || 0;
    const pct = total > 0 ? Math.min(100, (done / total) * 100) : 0;
    bar.value = pct;
    bytes.textContent = total ? `${formatBytes(done)} / ${formatBytes(total)}  (${pct.toFixed(0)}%)` : "";
    card.classList.remove("done", "error");
    switch (mp.status) {
      case "queued":
        status.textContent = "queued…";
        allDone = false;
        break;
      case "running":
        status.textContent = "downloading…";
        allDone = false;
        break;
      case "done":
        status.textContent = "✓ ready";
        card.classList.add("done");
        break;
      case "error":
        status.textContent = `✗ ${mp.error || "error"}`;
        card.classList.add("error");
        allDone = false;
        break;
    }
  }

  const nextBtn = $("#btn-download-next");
  if (nextBtn) nextBtn.disabled = !allDone;
  if (allDone) stopDownloadPoll();
}

function formatBytes(n) {
  if (n >= 1_000_000_000) return (n / 1_000_000_000).toFixed(2) + " GB";
  if (n >= 1_000_000)     return (n / 1_000_000).toFixed(1) + " MB";
  if (n >= 1_000)         return (n / 1_000).toFixed(0) + " KB";
  return `${n} B`;
}


// --------------------------------------------------------------------------
// Probes
// --------------------------------------------------------------------------

async function onTestSarvam() {
  const key = $("#sarvam-key").value.trim();
  state.sarvamKey = key;
  const out = $("#sarvam-result");
  out.hidden = false;
  out.className = "probe-result";
  out.textContent = "Testing…";

  let result;
  try { result = await Api.testSarvam(key); }
  catch (e) { renderProbe(out, "error", "Error: " + e.message); return; }

  if (result.overall === "ready") {
    const sample = result.sample ? ` <em>(translated "hello" → "${result.sample}")</em>` : "";
    renderProbe(out, "ok", "✓ " + result.message + sample, true);
    $("#btn-sarvam-next").disabled = false;
  } else {
    renderProbe(out, "error", "✗ " + (result.message || result.overall));
    $("#btn-sarvam-next").disabled = true;
  }
}

function renderProbe(el, kind, content, isHTML = false) {
  el.className = "probe-result " + kind;
  if (isHTML) el.innerHTML = content;
  else el.textContent = content;
}

// --------------------------------------------------------------------------
// Persist + redirect
// --------------------------------------------------------------------------

async function complete() {
  const payload = (state.engine === "local")
    ? {
        translator: "local-ai4bharat",
        tts: "local-ai4bharat",
        // No per-adapter settings — local pulls weights from public ungated
        // mirrors (naklitechie/*), so there's nothing for the user to configure.
        settings: { "local-ai4bharat": {} },
      }
    : {
        translator: "sarvam",
        tts: "sarvam",
        settings: { "sarvam": { api_key: state.sarvamKey } },
      };

  try {
    await Api.complete(payload);
  } catch (e) {
    alert("Couldn't save settings: " + e.message);
    throw e;
  }
}

// --------------------------------------------------------------------------
// Bootstrap
// --------------------------------------------------------------------------

go("pick");
