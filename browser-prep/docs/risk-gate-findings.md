# Risk gate: ONNX export of IndicTrans2

Run on **2026-04-28**, prashnam-voice venv: torch 2.11.0, optimum 2.1.0,
transformers 4.46.1, onnxruntime 1.25.1, onnxscript 0.7.0.

## TL;DR

**Yellow light.** Encoder exports cleanly via the new dynamo path. Decoder
export through `optimum-cli` silently produces an empty graph. Solvable,
but the path forward isn't `optimum-cli` + a custom OnnxConfig — it's
either a manual `torch.onnx.export` wrapper, a downgrade to a tested
optimum + torch combo, or the `transformers.js` upstream conversion
script which is purpose-built for this.

## What we tried

### Attempt 1 — vanilla `optimum-cli`
```
optimum-cli export onnx --model naklitechie/indictrans2-en-indic-dist-200M \
  --task text2text-generation-with-past --trust-remote-code \
  --opset 17 --device cpu ./it2-onnx
```
**Result:** `ValueError: Trying to export a IndicTrans model, that is a custom or unsupported architecture`. The model_type "IndicTrans" isn't in `TasksManager._SUPPORTED_MODEL_TYPE`, and CLI can't accept a programmatic OnnxConfig.

### Attempt 2 — programmatic `main_export` with custom `OnnxConfig`
Inherited from `M2M100OnnxConfig`. Wrote `IndicTransNormalizedConfig`
mapping IndicTrans's encoder_layers / decoder_layers / decoder_embed_dim
onto optimum's expected names. Wrote `IndicTransEncoderDummyGen` (a
`BartDummyTextInputGenerator` subclass) that uses `encoder_vocab_size`
(32322) instead of `vocab_size` (122672) for encoder dummy IDs.

**Result, sequenced:**

1. `TypeError: type 'DummySeq2SeqDecoderTextInputGenerator' is not subscriptable` — fixed by passing the canonical 3-tuple shape `(encoder_gen, {feature-extraction: …, text-generation: …}, {…past_kv_dict})`.
2. `ValueError: Config dummy inputs are not a subset of the model inputs: {'decoder_input_ids', 'input_ids', 'attention_mask'} vs ...` — fixed by registering `IndicTrans` in `TasksManager._SUPPORTED_MODEL_TYPE` so optimum routes via the **native** seq2seq path (`get_encoder_decoder_models_for_export` → `.with_behavior("encoder")` / `("decoder")`) instead of the custom-architecture path.
3. `KeyError: 'text2text-generation-with-past'` in `OnnxSeq2SeqConfigWithPast.outputs` — disappeared after step 2.
4. `RuntimeError: number of output names provided (1) exceeded number of outputs (0)` — fired by torch's legacy TorchScript exporter on the encoder. Optimum 2.1 hard-codes `dynamo=False` for torch >= 2.9, which forces this legacy path. Fixed by monkey-patching `optimum.exporters.onnx.convert.is_torch_version` to lie about the >= 2.9 check, so the new dynamo exporter is used.
5. `ModuleNotFoundError: No module named 'onnxscript'` — installed onnxscript 0.7.0.
6. `RuntimeError: # Failed to convert 'dynamic_axes' to 'dynamic_shapes'` — fixed by adding `--no-dynamic-axes` flag (we'll re-introduce dynamic shapes after a working static export).
7. `FileNotFoundError: encoder_model.onnx.data` — dynamo writes `<name>.onnx_data` (underscore) but optimum's cleanup expects `<name>.onnx.data` (period). Fixed by patching `os.remove` in `optimum.exporters.onnx.convert` to swallow `FileNotFoundError`.
8. `OrtValue indexes should have been populated` from onnxruntime in `config.fix_dynamic_axes()` — onnxruntime 1.25 can't load the dynamo-exported decoder. Patched `OnnxConfig.fix_dynamic_axes` to a no-op.
9. **Final state:** export script runs to completion. Encoder ONNX is valid (280 MB, 18 transformer layers, fp32). **Decoder ONNX is empty** (0 nodes, 0 outputs).

### Why the decoder is empty

For native seq2seq models, optimum's `_get_submodels_for_export_encoder_decoder` puts the **whole model** at the `decoder_model` slot:

```python
models_for_export[DECODER_NAME] = model      # full IndicTransForConditionalGeneration
```

The full model's forward expects `(input_ids, attention_mask, decoder_input_ids, …)`. But the dummy inputs we generate via M2M100's seq2seq input schema produce `(encoder_attention_mask, input_ids, encoder_hidden_states)`. Mismatch → dynamo's `torch.export.export` traces a no-op graph, silently emits an empty ONNX. The "Optimize the ONNX graph ✅" log fires because optimization on an empty graph is trivially successful.

Patching this requires either:

- A `fn_get_submodels` callback that returns the actual decoder-only subnet, OR
- A custom `forward` wrapper that accepts the renamed inputs and routes them through `model(decoder_input_ids=…, encoder_outputs=…, attention_mask=…)`.

Either is doable, but at this point we've shimmed optimum in five places. Each fix exposes a new layer.

## What works

- **Encoder export** is clean. 280 MB ONNX, weights externalised, valid graph. Confirmed via `onnx.load` + manual node count.
- The architecture inspection: 18 enc/dec layers, 8 heads, d_model=512, source vocab 32322, target vocab 122672 — fits the M2M100 / NLLB family, no surprises.
- Custom `OnnxConfig` inheritance works once the right registry slot is poked.

## Three forward paths

### A. Manual `torch.onnx.export` (recommended)

Skip `optimum` entirely. Write three small Python functions:

1. `export_encoder(model, out_path)` — wraps `model.get_encoder()` with a clean
   forward, calls `torch.onnx.export(..., dynamo=True)`.
2. `export_decoder_first_step(model, out_path)` — wraps the full model with a
   `forward(input_ids, attention_mask, decoder_input_ids)` that runs the
   encoder then the decoder.
3. `export_decoder_with_past(model, out_path)` — same, but with
   `past_key_values` plumbed through.

About 200 LOC. Each wrapper is auditable end-to-end. We've already proven
the encoder works via this path — same machinery for the decoder, no
optimum middleware to fight.

**Why this is the recommendation:** every patch we made to optimum is one
more thing to maintain. A 200-LOC manual export script has zero
dependencies on optimum's quirks, can be tested in isolation, and is the
standard pattern for converting custom architectures (the
`transformers.js` upstream `scripts/convert.py` follows this approach).

### B. `transformers.js` upstream conversion script

The `huggingface/transformers.js` repo ships
[`scripts/convert.py`](https://github.com/huggingface/transformers.js/blob/main/scripts/convert.py).
It's purpose-built for browser-loadable ONNX, handles `trust_remote_code`
models, applies q4f16 quantization in one pass. Likely the same recipe
the `Xenova/*` and `onnx-community/*` model repos use. Probably already
solves the decoder issue we hit.

Cost: clone a separate repo, run a separate venv with their pinned
torch/optimum versions. We keep our prashnam-voice venv clean.

### C. Downgrade torch + optimum

torch 2.11 is bleeding-edge. Pin to torch 2.5 + optimum 1.21 (a known-
working combo for seq2seq export). Likely fixes most of the patches above
because the legacy TorchScript exporter would handle ModelOutput
dataclasses cleanly.

Cost: invasive — affects the prashnam-voice venv that's currently happy.
Would need a separate `browser-prep/.venv` for the export work.

## Recommendation

**Path A first** (manual `torch.onnx.export`). Estimate: half a day.
Lowest dependency surface, highest auditability. We've already proven
the encoder works — duplicating that pattern for the decoder is a small
incremental task.

If A bumps into something fundamental, fall back to B
(`transformers.js`'s conversion script in a separate venv).

C only if A and B both fail and we need to debug from the bottom up.

## Files emitted by the partial export (current state)

```
browser-prep/scratch/it2-onnx/
├── config.json                       1.5 KB
├── encoder_model.onnx                1.6 MB        ✅ valid graph
├── encoder_model.onnx_data         280.1 MB        ✅ encoder weights
├── decoder_model.onnx               86 KB          ❌ empty graph (0 nodes)
├── decoder_model.onnx.data           0 B           ❌ empty
└── generation_config.json          163 B
```

The encoder file IS browser-loadable as-is. We could ship a translation-
forward demo with just the encoder and a JS-side beam search if the
decoder problem proves unsolvable — but that's wasteful (we're throwing
away 18 layers of decoder weights). Path A should fix this properly.
