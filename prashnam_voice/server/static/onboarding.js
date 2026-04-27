// Onboarding wizard. Two parallel paths share the rail/stage layout:
//
//   local  → pick → hf-account → tos-it2 → tos-parler → token (test) → done
//   sarvam → pick → sarvam-account → sarvam-key (test) → done
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
    { id: "pick",        label: "Engine"          },
    { id: "hf-account",  label: "HF account"      },
    { id: "tos-it2",     label: "Translator T&Cs" },
    { id: "tos-parler",  label: "TTS T&Cs"        },
    { id: "token",       label: "Token"           },
    { id: "download",    label: "Download"        },
    { id: "done",        label: "Done"            },
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
  hfToken: "",
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
  testHf:    (token)   => api("POST", "/api/onboarding/test-hf", { token }),
  testSarvam:(api_key) => api("POST", "/api/onboarding/test-sarvam", { api_key }),
  complete:  (payload) => api("POST", "/api/onboarding/complete", payload),
  startDownload: (token) => api("POST", "/api/onboarding/download-models", { token: token || null }),
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
  if (step === "hf-account")     return tpl("step-hf-account");
  if (step === "tos-it2")        return tpl("step-hf-tos-it2");
  if (step === "tos-parler")     return tpl("step-hf-tos-parler");
  if (step === "token")          return tpl("step-hf-token");
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
    if (b.id === "btn-hf-next" || b.id === "btn-sarvam-next") return;  // handled below
    b.addEventListener("click", () => goNext());
  });

  if (step === "pick") {
    $("#btn-pick-next").addEventListener("click", () => {
      const picked = document.querySelector('input[name="engine"]:checked');
      state.engine = picked ? picked.value : "local";
      goNext();
    });
  }

  if (step === "token") {
    const input = $("#hf-token");
    if (state.hfToken) input.value = state.hfToken;
    $("#btn-test-hf").addEventListener("click", onTestHf);
    // For local: persist config (saves the token), then move on to the
    // download step where the wizard streams progress.
    $("#btn-hf-next").addEventListener("click", () => {
      state.hfToken = input.value;
      complete().then(() => goNext());
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
  Api.startDownload(state.hfToken).catch(() => { /* server-side error surfaces via progress poll */ });
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

async function onTestHf() {
  const token = $("#hf-token").value.trim();
  state.hfToken = token;
  const out = $("#hf-result");
  out.hidden = false;
  out.className = "probe-result";
  out.textContent = "Testing…";

  let result;
  try { result = await Api.testHf(token); }
  catch (e) { renderProbe(out, "error", "Error: " + e.message); return; }

  if (result.overall === "ready") {
    renderProbe(out, "ok", "✓ " + (result.message || "Token works."));
    $("#btn-hf-next").disabled = false;
  } else if (result.overall === "models_not_accepted") {
    const lines = (result.models || [])
      .filter((m) => m.status === "needs_acceptance")
      .map((m) => `<li><strong>${m.model_id}</strong> — open the model page and click <em>Agree and access</em>.</li>`)
      .join("");
    renderProbe(out, "warn",
      `Token works, but you still need to accept the licence on:<ul>${lines}</ul>`,
      true);
    $("#btn-hf-next").disabled = true;
  } else if (result.overall === "token_invalid") {
    renderProbe(out, "error", "✗ " + (result.message || "Token rejected."));
    $("#btn-hf-next").disabled = true;
  } else {
    renderProbe(out, "error", "✗ " + (result.message || "Unknown error."));
    $("#btn-hf-next").disabled = true;
  }
}

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
        // HF token isn't required by our local adapter (huggingface_hub picks
        // it up from huggingface-cli login or HF_TOKEN env). We persist it on
        // the local adapter setting bag for symmetry; the local adapter
        // currently ignores it, but a future revision can read it to set
        // HF_TOKEN before model download.
        settings: { "local-ai4bharat": { hf_token: state.hfToken } },
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
