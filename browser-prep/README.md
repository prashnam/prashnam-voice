# browser-prep — model conversion + JS porting workshop

Scratch space for preparing an in-browser version of prashnam-voice
(provisional name: **Anuvaad**, हिंदी for "translation"). This folder
is **not** runtime code for prashnam-voice — it's the workshop where
we convert AI4Bharat models to ONNX, port the Python preprocessing
pipeline to JavaScript, and validate parity before opening a separate
single-file browser project.

When the work here is green, the artifacts split three ways:

| Artifact | Destination |
|---|---|
| `*.onnx` model bundles | `naklitechie/indictrans2-...-ONNX` on Hugging Face |
| `tokenizer.json`, `config.json` | Same HF repo |
| `js/indic_processor.js`, `js/lexicon.js`, etc. | Inlined into `Anuvaad/index.html` (the new browser repo, sibling to LocalMind / BabelLocal under `/Users/chiragpatnaik/Code/Browser/`) |

## Layout

```
browser-prep/
├── README.md            (this file)
├── docs/
│   └── browser-pack-spec.md   — the API surface the browser app consumes
├── fixtures/
│   ├── test_sentences.json    — ~200 EN inputs across politics / generic / numerals / lexicon
│   └── expected_outputs.json  — PyTorch + IndicProcessor ground truth (all 23 langs)
├── scripts/
│   ├── 01_export_onnx.py      — optimum-cli wrapper (fp32 + fp16 + q4f16)
│   ├── 02_build_tokenizer.py  — SentencePiece → HF tokenizer.json
│   ├── 03_capture_truth.py    — runs the Python pipeline on fixtures
│   ├── 04_parity_test.py      — loads ONNX, runs through fixtures, computes BLEU vs truth
│   └── 05_publish_hf.py       — pushes the ONNX bundle to naklitechie/<repo>-ONNX
├── js/
│   ├── indic_processor.js     — port of IndicProcessor.{preprocess,postprocess}_batch
│   ├── indic_processor.test.js — parity tests against fixtures (run in node)
│   ├── tokenizer.js           — Transformers.js tokenizer wrapper for IT2 quirks
│   └── lexicon.js             — port of the prashnam_voice lexicon system
└── scratch/                   — ad-hoc experiments, intermediate exports (gitignored)
```

## Sequence

The work is gated by an early experiment: we don't know yet whether
`optimum-cli export onnx` handles IndicTrans2's custom modeling code
cleanly. Risk gate first, full build second.

1. **Risk gate** — run `01_export_onnx.py` in dry-run mode against
   `naklitechie/indictrans2-en-indic-dist-200M` and inspect what comes
   out. Three outcomes:
   - Clean ONNX files → green light. Proceed.
   - Export fails on custom architecture → patch `modeling_indictrans.py`
     to be optimum-friendly, or fork to a different base model.
   - Clean export but quantized quality regresses → tune quantization
     scheme; q8 or layer-wise mixed precision.

2. **Tokenizer conversion** — IndicTrans2 ships SentencePiece
   `model.SRC` / `model.TGT` files. Convert to a `tokenizer.json` that
   Transformers.js's BPE tokenizer can consume natively (avoids
   bundling a SentencePiece WASM runtime).

3. **IndicProcessor JS port** — the load-bearing 300–500 LOC of
   preprocessing (script normalization, language tag injection,
   detokenization quirks). Validate against the Python ground truth
   on all 23 langs at sentence-level exact match.

4. **End-to-end smoke** — run the whole pipeline (preprocess → ONNX →
   detokenize → postprocess) in node against the fixtures. If BLEU
   drift vs. the Python ground truth is < 1.0 points averaged across
   languages, ship.

5. **Publish to HF** — push the ONNX bundle + tokenizer + NOTICE.md
   to `naklitechie/indictrans2-en-indic-dist-200M-ONNX`.

6. **Open the browser project** at `/Users/chiragpatnaik/Code/Browser/Anuvaad/`.
   `index.html` follows the LocalMind / BabelLocal single-file
   convention, consumes the HF artifacts, inlines the JS port.

## What's deliberately out of scope

- **Indic Parler-TTS in the browser.** Different problem (4-component
  architecture, ~1B params, no upstream ONNX, autoregressive generation
  at thousands of timesteps). Tracked separately if/when the translation
  app proves out the foundation.
- **Per-language quantization tuning.** Default to q4f16 for the browser
  ship; revisit only if specific languages regress badly.
- **Voice / pace controls.** The browser app is text-in, text-out — the
  audio side stays in prashnam-voice (the Python tool).
