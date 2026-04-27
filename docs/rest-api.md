# prashnam-voice REST API

The local server (`prashnam-voice serve`) exposes a JSON HTTP API on
`http://127.0.0.1:8765` by default. The web app uses it; the same surface
is fair game for scripts, automations, and other front-ends.

| | |
|---|---|
| **Base URL** | `http://127.0.0.1:8765/api` (everything below is relative) |
| **Auth** | none — single-user local server. `--host 127.0.0.1` is the default |
| **Content type** | `application/json` for request + response (multipart for file upload) |
| **CORS** | `Access-Control-Allow-Origin: *`. Safe because the server only binds to localhost; required so a `file://` bootstrap page can poll `/api/health` |
| **Errors** | non-2xx returns `{"detail": "..."}` per FastAPI default. Status codes used: `400` invalid input, `404` not found, `409` conflict, `500` server error |

For embedding the same engine in Python instead of going over HTTP, see
[**python-api.md**](python-api.md).

## Conventions used in this document

- Path params shown as `{pid}`, `{sid}`, `{lang}`, `{rotation_id}`, etc.
- "Shapes" use TypeScript-style notation: `{name: string, langs?: string[]}`.
- `?` after a field name = optional. `null`-ability shown explicitly when relevant.
- The "rotation_id" path segment is `r0` (canonical / declared order) or
  `r1`, `r2`, … when option-order rotations are enabled. Audio + attempts
  paths without a rotation segment are shorthand for `r0`.

---

## Reference data

Reference endpoints don't depend on any project state. Cache freely.

### `GET /api/health`

Lightweight liveness + onboarding-state probe. Polled by the bootstrap
`index.html` (over `file://`) to detect when the server is up.

**Response** `200`:
```json
{
  "status": "ok",
  "version": "0.1.0",
  "onboarded": true,
  "translator": "local-ai4bharat",
  "tts": "local-ai4bharat"
}
```

### `GET /api/languages`

The 11 supported language codes, in display order. English is first.

**Response** `200`:
```json
[
  {"code": "en", "name": "English",   "voice": "Aditi"},
  {"code": "hi", "name": "Hindi",     "voice": "Divya"},
  {"code": "ta", "name": "Tamil",     "voice": "Jaya"},
  ...
]
```

### `GET /api/paces`

Pace enum + the project default.

**Response** `200`:
```json
{
  "options": ["very_slow", "slow", "moderate", "fast", "very_fast"],
  "default": "moderate"
}
```

### `GET /api/domains`

All registered domain packs (built-ins: `poll`, `announcement`).

**Response** `200`:
```json
[
  {
    "name": "poll",
    "label": "Poll",
    "description": "1 question + N options.",
    "segment_types": [
      {"name": "question", "label": "Question", "addable": false, "deletable": false, "max": 1, "template_field": "question_template"},
      {"name": "option",   "label": "Option",   "addable": true,  "deletable": true,  "max": null, "template_field": "option_template"}
    ],
    "default_templates": {
      "question_template": "Namaskar, this is a call from Prashnam, an independent polling agency. {body}",
      "option_template":   "If you think {body}, then press {n}."
    }
  },
  {"name": "announcement", "...": "..."}
]
```

---

## Onboarding

These endpoints power the in-app first-run wizard.

### `POST /api/onboarding/test-hf`

Probe Hugging Face Hub access for both gated AI4Bharat models with the
user's read token. Status codes on the *probe responses* map cleanly:
`200` → token + ToS both fine, `401` → bad token, `403` → ToS not yet
accepted (with the offending model id named).

**Body**: `{"token": string}`

**Response** `200`:
```json
{
  "overall": "ready" | "token_invalid" | "models_not_accepted" | "error",
  "message": "string",
  "models": [
    {
      "model_id": "ai4bharat/indictrans2-en-indic-dist-200M",
      "status":   "ok" | "needs_acceptance" | "bad_token" | "error",
      "detail":   "string"
    }
  ]
}
```

### `POST /api/onboarding/test-sarvam`

Probe a Sarvam.ai API key by translating "hello" to Hindi.

**Body**: `{"api_key": string}`

**Response** `200`:
```json
{
  "overall": "ready" | "key_invalid" | "quota" | "error",
  "message": "string",
  "sample":  "नमस्ते"
}
```

### `POST /api/onboarding/complete`

Persist the chosen adapter pair + per-adapter settings to
`~/.config/prashnam-voice/config.json` and flip the `onboarded` flag.
Drops cached engines so the next inference picks up the new adapter.

**Body**:
```json
{
  "translator": "local-ai4bharat" | "sarvam",
  "tts":        "local-ai4bharat" | "sarvam",
  "settings": {
    "<adapter_name>": {"api_key": "...", "hf_token": "..."}
  }
}
```

**Response** `200`: `{"ok": true}`. **`400`** if the adapter name is unknown.

---

## Projects

A project is a directory under the configured `projects_root` containing
`project.json` and an `audio/` tree.

### `GET /api/projects`

Compact listing for the project list page. Sorted newest-updated first.

**Response** `200`:
```json
[
  {
    "id": "election-2026-ab12cd",
    "name": "Election 2026",
    "created_at": "2026-04-27T12:34:56.789012+00:00",
    "updated_at": "2026-04-27T15:00:00.000000+00:00",
    "segment_count": 4,
    "langs": ["en", "hi", "ta"]
  }
]
```

### `POST /api/projects`

Create a new project.

**Body**:
```json
{
  "name":   "Election 2026",
  "langs":  ["en", "hi", "ta"],
  "domain": "poll" | "announcement"
}
```
- `langs` defaults to all 11 if omitted.
- `domain` defaults to `poll`.

**Response** `200`: full project (see `GET /api/projects/{pid}` shape).

### `GET /api/projects/{pid}`

Full project state.

**Response** `200`:
```json
{
  "id": "election-2026-ab12cd",
  "name": "Election 2026",
  "created_at": "...",
  "updated_at": "...",
  "domain": "poll",
  "langs": ["en", "hi", "ta"],
  "default_pace": "moderate",
  "voices": {"hi": "Divya"},
  "paces": {"ta": "slow"},
  "question_template": "Namaskar, ... {body}",
  "option_template":   "If you think {body}, then press {n}.",
  "body_template":     "",
  "rotation_count": 1,
  "rotation_seed": null,
  "rotations": [],
  "lexicon": {"global": {"BJP": "bee jay pee"}, "hi": {"BJP": "बीजेपी"}},
  "segments": [
    {
      "id": "seg_abc123",
      "type": "question",
      "english": "Who will win?",
      "use_template": true,
      "lock_at_end": false,
      "translations": {"hi": {"r0": "कौन जीतेगा?"}},
      "current_takes": {"hi": {"r0": "att_x"}}
    }
  ]
}
```

### `PATCH /api/projects/{pid}`

Update settings. All fields optional; only present fields are changed.

**Body** (any subset):
```json
{
  "name": "string",
  "langs": ["en", "hi"],
  "default_pace": "moderate",
  "voices":  {"hi": "Divya"},
  "paces":   {"ta": "slow"},
  "question_template": "string",
  "option_template":   "string",
  "lexicon": {"global": {"BJP": "bee jay pee"}}
}
```

**Side effects**: changing `question_template` / `option_template` /
`lexicon` clears all cached translations + current takes for affected
segments (those need a regenerate to refresh).

**Response** `200`: full project. **`400`** on invalid lang code, unknown
pace, etc.

### `DELETE /api/projects/{pid}`

Remove the project's directory and all audio. **No "are you sure"** on the
server side — UI-level confirmation expected. Returns `{"deleted": "<pid>"}`.

### `POST /api/projects/{pid}/open-folder`

Asks the OS to open the project folder in Finder / Explorer / a file
manager. macOS uses `open`, Linux `xdg-open`, Windows `explorer`.

**Response** `200`: `{"opened": "/abs/path/to/project"}`. **`501`** on an
unsupported platform. Not useful from non-localhost clients.

### `GET /api/projects/{pid}/zip`

Download all *current takes* for the project as a zip.

**Response** `200`: `application/zip`, content-disposition attachment.

Layout inside the zip:
- single-rotation projects → `<lang>/{question,option_1,...}.mp3`
- multi-rotation projects  → `r0/<lang>/{question,option_1,...}.mp3`,
  `r1/<lang>/...`, etc. The `option_N.mp3` ordering matches *that
  rotation's* permutation (so `option_1.mp3` is the option you'd press
  `1` for, after the rotation).

`project.json` is included verbatim at the zip root.

### `POST /api/projects/import`

Bulk-create projects from a CSV. Multipart only.

**Form fields**:
- `file` (file upload) — required
- `domain` — `poll` (default) or `announcement`
- `langs` — comma-separated codes; empty = all 11

CSV schemas (column order doesn't matter; `name` and `langs` columns are
optional):

| Domain         | Required headers                | Optional headers |
|---             |---                              |---               |
| `poll`         | `group_id, type, english`       | `name`, `langs`  |
| `announcement` | `group_id, english`             | `name`, `langs`  |

For polls, `type` is `question` or `option` (case-insensitive). Rows
sharing a `group_id` form one project. Comments (`#…`) and blank lines
are ignored. `langs` cell value is `|`-separated (e.g. `hi|ta|en`).

**Response** `200`:
```json
{
  "created": [{"id": "election-2026-ab12cd", "name": "Election 2026", "segments": 4}],
  "rows_consumed": 12,
  "errors": [{"line": 7, "message": "missing english text"}]
}
```
Bad rows are non-fatal — good rows still import. **`400`** on unknown
domain or unknown lang code.

---

## Segments

Segment types depend on the project's domain (`poll`: `question`,
`option`; `announcement`: `body`).

### `POST /api/projects/{pid}/segments`

Add a segment.

**Body**: `{"type": "question" | "option" | "body", "english": "string"}`

**Response** `200`:
```json
{
  "segment_id": "seg_abc123",
  "project": { "...full project..." }
}
```

**`400`** if the type isn't allowed in the project's domain or violates a
type cap (e.g. polls cap `question` at 1).

### `PATCH /api/projects/{pid}/segments/{sid}`

Edit a segment's English text. Changing the text invalidates all of its
translations + current takes (all rotations).

**Body**: `{"english": "string"}`

**Response** `200`:
```json
{
  "segment": { "...segment..." },
  "invalidated_langs": ["hi", "ta"]
}
```

### `DELETE /api/projects/{pid}/segments/{sid}`

Remove the segment + its on-disk audio directory. Returns
`{"deleted": "<sid>", "project": {...}}`.

### `PATCH /api/projects/{pid}/segments/{sid}/template`

Toggle whether the project template wraps this segment. Clears that
segment's translations + current takes (its effective text changes).

**Body**: `{"use_template": boolean}`

**Response** `200`: `{"segment": {...}}`

### `PATCH /api/projects/{pid}/segments/{sid}/lock`

Pin an option to the last position in every rotation (the NOTA pattern).
Triggers a recompute of the project's rotations if rotations are active.

**Body**: `{"lock_at_end": boolean}`

**Response** `200`: `{"segment": {...}, "project": {...}}`. **`400`** if
the segment is not an option.

---

## Option-order rotations

Rotations are off by default (`rotation_count == 1`). Once enabled,
`rotation_count` distinct orderings of the option segments are
persisted; locked options stay last in every rotation.

### `POST /api/projects/{pid}/rotations/enable`

**Body**:
```json
{
  "count": 3,
  "seed": 42,
  "lock_last_as_nota": true
}
```
- `count` ≥ 2 (required).
- `seed` optional; `null` = system random.
- `lock_last_as_nota` — if `true`, marks the last option in declared
  order as `lock_at_end` before computing rotations.

**Response** `200`: full project. **`400`** if `count < 2`.

### `POST /api/projects/{pid}/rotations/disable`

Collapse back to a single (canonical) ordering. Audio for non-canonical
rotations stays on disk but won't be served via rotation-aware paths.

**Response** `200`: full project.

### `POST /api/projects/{pid}/rotations/reshuffle`

Recompute rotation orderings (useful after adding an option or wanting a
different shuffle). The canonical row (`rotations[0]`) always stays
unchanged.

**Body**: `{"seed": int | null}`. `null` keeps the existing seed; an int
overrides + persists.

**Response** `200`: full project.

---

## Regeneration jobs

### `POST /api/projects/{pid}/segments/{sid}/regenerate`

Translate (if missing) and synthesize audio for one segment in one or
more languages, optionally scoped to specific rotations. Returns
immediately with a `job_id`; the work runs on the server's two-stage
queue (translate → audio).

**Body**:
```json
{
  "langs": ["hi", "ta"],
  "rotation_ids": ["r0", "r1"]
}
```
- `langs` required + non-empty; must be a subset of the project's `langs`.
- `rotation_ids` optional; omit/`null` = every active rotation. Question
  and body segments only ever produce r0; passing other rotation ids on
  them is silently coerced.

**Response** `200`: `{"job_id": "abc123def456"}`. **`400`** on unknown
langs or rotation ids.

### `GET /api/jobs`

Active (queued + running) jobs in submission order.

**Response** `200`:
```json
[
  {
    "id": "abc123def456",
    "status": "queued" | "running" | "done" | "error",
    "error": null,
    "elapsed_s": 4.2,
    "by_lang": {
      "hi": {"translated": true, "audio_started": true, "audio_done": 1, "audio_total": 3, "cache_hits": 0}
    },
    "translations": null,
    "project_id": "election-2026-ab12cd",
    "segment_id": "seg_abc123",
    "new_attempts": {"hi::r0": "att_xyz"},
    "run_id": ""
  }
]
```
For multi-rotation regens, `audio_total` is the number of rotations being
synthesized for that lang and `audio_done` counts completed rotations.
`new_attempts` is keyed `<lang>::<rotation_id>`.

Completed jobs are dropped from the active list but remain queryable via
`/api/jobs/{job_id}` for a short window.

### `GET /api/jobs/{job_id}`

Same shape as one entry of `/api/jobs`. **`404`** if the job id is
unknown (already cleaned up or never existed).

---

## Takes (audio)

### `POST /api/projects/{pid}/segments/{sid}/select`

Pick a previous attempt as the current take for one (lang, rotation)
slot. Useful when re-rolls produced a worse clip than an older take.

**Body**:
```json
{
  "lang": "hi",
  "attempt_id": "att_xyz789",
  "rotation_id": "r0"
}
```

**Response** `200`: `{"segment": {...}}`. **`404`** if the attempt or
project doesn't exist; **`400`** for unknown lang.

### `GET /api/projects/{pid}/segments/{sid}/attempts/{lang}`

List all attempts for `(segment, lang, r0)`. Sorted newest first.

**Response** `200`:
```json
{
  "attempts": [
    {
      "id": "att_xyz789",
      "segment_id": "seg_abc123",
      "lang": "hi",
      "rotation_id": "r0",
      "voice": "Divya",
      "pace": "moderate",
      "source_text": "कौन जीतेगा?",
      "duration_s": 2.4,
      "model_id": "ai4bharat/indic-parler-tts",
      "created_at": "2026-04-27T15:30:00.000000+00:00"
    }
  ]
}
```
Surfaces legacy (pre-rotation) attempts as r0 too.

### `GET /api/projects/{pid}/segments/{sid}/attempts/{lang}/{rotation_id}`

Same as above but for a specific rotation (`r0` … `r{n-1}`).

### `GET /api/projects/{pid}/audio/{sid}/{lang}/{name}`

Stream an MP3 for a (segment, lang, r0) attempt. `{name}` is
`<attempt_id>.mp3`. Falls back to a pre-rotation on-disk layout for
legacy projects.

**Response** `200`: `audio/mpeg`.

### `GET /api/projects/{pid}/audio/{sid}/{lang}/{rotation_id}/{name}`

Rotation-aware audio path. Required for any rotation other than `r0`.

---

## Legacy (one-shot CLI)

### `POST /api/generate`

Drives the legacy `prashnam-voice generate ...` CLI flow. Creates a
`run_id` under the configured `out_root` and writes flat
`<lang>/{question,option_N}.mp3` files. Not used by the web app.

**Body**:
```json
{
  "question": "Who will win?",
  "options": ["Party A", "Party B"],
  "langs": ["hi", "ta"],
  "voices": {"hi": "Divya"},
  "pace": "moderate",
  "paces": {"ta": "slow"}
}
```

**Response** `200`: `{"job_id": "abc..."}`. Poll `/api/jobs/{job_id}` for
progress. **`400`** for invalid input.

### `GET /api/jobs/{job_id}/audio/{lang}/{name}`

Stream an MP3 from a legacy `/api/generate` run (under `out_root`, not
the rotation-aware project layout).

---

## Pages (HTML)

Not part of the JSON API but useful to know:

| Path | Purpose |
|---|---|
| `GET /` | Main app if `onboarded`, otherwise serves the wizard at the same URL |
| `GET /onboarding` | Wizard HTML (always available regardless of state) |
| `GET /static/*` | Frontend assets |

---

## Job queue model

Two queues, one worker each:

1. **Translate queue** (fast). Each `regenerate` request submits a
   single translate task that fans out per-rotation translation calls,
   then enqueues per-rotation audio sub-tasks.
2. **Audio queue** (slow, FIFO across all jobs). Each task synthesizes
   one `(segment, lang, rotation)` clip.

Effect: when several segments are queued, *every* job's translations
land within seconds, while the audio queue grinds them out one
synthesis at a time. The web app's queue panel reflects this.

---

## Versioning

This API is **pre-1.0** and may change. Breaking changes will be called
out in the changelog. The endpoints under `/api/onboarding` and the
legacy `/api/generate` + `/api/jobs/{id}/audio/...` are most likely to
shift; the project + segment + rotation paths are the stable core.
