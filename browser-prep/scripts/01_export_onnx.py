#!/usr/bin/env python3
"""
01_export_onnx.py — convert IndicTrans2 to ONNX.

`optimum-cli export onnx` doesn't recognise the custom `IndicTrans` model
type, so we plug in a custom OnnxConfig class that inherits from M2M100's
config (which it most resembles structurally) and register it with the
TasksManager before invoking `main_export` programmatically.

Usage:
    python 01_export_onnx.py \\
        --model-id naklitechie/indictrans2-en-indic-dist-200M \\
        --out-dir ../scratch/it2-onnx \\
        --dtype fp32

The exported folder ships with `encoder_model.onnx`, `decoder_model.onnx`,
`decoder_with_past_model.onnx`, plus the tokenizer + config bundle.
"""
from __future__ import annotations

import argparse
import logging
import sys
from pathlib import Path
from typing import Mapping

# ---------------------------------------------------------------------------
# Logging setup
# ---------------------------------------------------------------------------

logging.basicConfig(
    level=logging.INFO,
    format="[%(asctime)s] %(levelname)s %(name)s: %(message)s",
    datefmt="%H:%M:%S",
)
log = logging.getLogger("export_onnx")


# ---------------------------------------------------------------------------
# Imports — done lazily after argparse so --help is fast
# ---------------------------------------------------------------------------

def _import_export_deps():
    from optimum.exporters.onnx import main_export
    from optimum.exporters.onnx.model_configs import M2M100OnnxConfig
    from optimum.exporters.tasks import TasksManager
    from optimum.utils import (
        DummyDecoderTextInputGenerator,
        DummyPastKeyValuesGenerator,
        DummySeq2SeqDecoderTextInputGenerator,
        DummySeq2SeqPastKeyValuesGenerator,
        DummyTextInputGenerator,
        NormalizedSeq2SeqConfig,
    )
    from optimum.utils.input_generators import BartDummyTextInputGenerator
    return {
        "main_export": main_export,
        "M2M100OnnxConfig": M2M100OnnxConfig,
        "TasksManager": TasksManager,
        "BartDummyTextInputGenerator": BartDummyTextInputGenerator,
        "DummyDecoderTextInputGenerator": DummyDecoderTextInputGenerator,
        "DummyPastKeyValuesGenerator": DummyPastKeyValuesGenerator,
        "DummySeq2SeqDecoderTextInputGenerator": DummySeq2SeqDecoderTextInputGenerator,
        "DummySeq2SeqPastKeyValuesGenerator": DummySeq2SeqPastKeyValuesGenerator,
        "DummyTextInputGenerator": DummyTextInputGenerator,
        "NormalizedSeq2SeqConfig": NormalizedSeq2SeqConfig,
    }


# ---------------------------------------------------------------------------
# Custom OnnxConfig for IndicTrans
# ---------------------------------------------------------------------------

def build_indictrans_onnx_config(deps):
    """Define a custom OnnxConfig that handles IndicTrans's quirks.

    Two deviations from M2M100:
      1. `vocab_size` for IndicTrans equals `decoder_vocab_size` (122672),
         but the encoder accepts a smaller vocab (32322). The default dummy
         input generator uses `vocab_size` for both, which would feed the
         encoder out-of-range token IDs and crash. We override the encoder
         dummy generator to use `encoder_vocab_size`.
      2. The config uses `encoder_embed_dim` / `decoder_embed_dim` instead
         of M2M100's `d_model`. Optimum's `NormalizedSeq2SeqConfig` reads
         `hidden_size`; we map it from `decoder_embed_dim`.

    Optimum's seq2seq machinery expects DUMMY_INPUT_GENERATOR_CLASSES to
    be a 3-tuple: (encoder_text_gen, decoder_dict, past_kv_dict). We
    preserve that shape, only swapping the encoder generator for our
    encoder-vocab-aware variant.
    """
    M2M100OnnxConfig                     = deps["M2M100OnnxConfig"]
    BartDummyTextInputGenerator          = deps["BartDummyTextInputGenerator"]
    DummyDecoderTextInputGenerator       = deps["DummyDecoderTextInputGenerator"]
    DummyPastKeyValuesGenerator          = deps["DummyPastKeyValuesGenerator"]
    DummySeq2SeqDecoderTextInputGenerator = deps["DummySeq2SeqDecoderTextInputGenerator"]
    DummySeq2SeqPastKeyValuesGenerator   = deps["DummySeq2SeqPastKeyValuesGenerator"]
    NormalizedSeq2SeqConfig              = deps["NormalizedSeq2SeqConfig"]

    class IndicTransNormalizedConfig(NormalizedSeq2SeqConfig):
        # Map IndicTrans config keys onto the names optimum expects.
        ENCODER_NUM_LAYERS = "encoder_layers"
        DECODER_NUM_LAYERS = "decoder_layers"
        NUM_LAYERS = "decoder_layers"
        ENCODER_NUM_ATTENTION_HEADS = "encoder_attention_heads"
        DECODER_NUM_ATTENTION_HEADS = "decoder_attention_heads"
        EOS_TOKEN_ID = "eos_token_id"
        # IndicTrans names hidden size differently
        HIDDEN_SIZE = "decoder_embed_dim"
        VOCAB_SIZE = "decoder_vocab_size"

    class IndicTransEncoderDummyGen(BartDummyTextInputGenerator):
        """Same as Bart's default, but uses encoder_vocab_size for token IDs."""
        def __init__(self, task, normalized_config, *args, **kwargs):
            super().__init__(task, normalized_config, *args, **kwargs)
            cfg = normalized_config.config
            enc_vocab = getattr(cfg, "encoder_vocab_size", None)
            if enc_vocab is not None:
                self.vocab_size = enc_vocab

    class IndicTransOnnxConfig(M2M100OnnxConfig):
        NORMALIZED_CONFIG_CLASS = IndicTransNormalizedConfig
        DUMMY_INPUT_GENERATOR_CLASSES = (
            IndicTransEncoderDummyGen,
            {
                "feature-extraction": DummySeq2SeqDecoderTextInputGenerator,
                "text-generation": DummyDecoderTextInputGenerator,
            },
            {
                "feature-extraction": DummySeq2SeqPastKeyValuesGenerator,
                "text-generation": DummyPastKeyValuesGenerator,
            },
        )
        # Same forward shape as M2M100 → keep its tolerance.
        ATOL_FOR_VALIDATION = 1e-3

    return IndicTransOnnxConfig


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main() -> int:
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument(
        "--model-id",
        default="naklitechie/indictrans2-en-indic-dist-200M",
        help="HF model repo to export (default: %(default)s)",
    )
    p.add_argument(
        "--out-dir",
        default="../scratch/it2-onnx",
        help="Where to write the ONNX bundle (default: %(default)s)",
    )
    p.add_argument(
        "--task",
        default="text2text-generation-with-past",
        help="Optimum task name (default: %(default)s)",
    )
    p.add_argument(
        "--dtype",
        default="fp32",
        choices=("fp32", "fp16", "bf16"),
        help="Tensor dtype to export (default: %(default)s)",
    )
    p.add_argument(
        "--opset",
        type=int,
        default=17,
        help="ONNX opset version (default: %(default)s)",
    )
    p.add_argument(
        "--no-validate",
        action="store_true",
        help="Skip the post-export validation pass (faster, less safe).",
    )
    p.add_argument(
        "--no-dynamic-axes",
        action="store_true",
        help="Bake all input shapes into the ONNX graph (no dynamic seq_len, batch).",
    )
    args = p.parse_args()

    out_dir = Path(args.out_dir).resolve()
    out_dir.mkdir(parents=True, exist_ok=True)

    log.info("Importing optimum / transformers …")
    deps = _import_export_deps()
    main_export = deps["main_export"]
    TasksManager = deps["TasksManager"]

    # Optimum 2.1 hard-codes `dynamo=False` for torch >= 2.9 (legacy
    # TorchScript exporter). The legacy path can't handle IndicTrans's
    # BaseModelOutput-shaped encoder output ("number of outputs (0)"
    # error during graph trace). The new dynamo path on torch 2.11+
    # works fine but requires onnxscript. Monkey-patch the version
    # gate so optimum's branch lands in the empty-kwargs path (i.e.
    # uses torch's default, which is dynamo=True on 2.9+).
    import optimum.exporters.onnx.convert as _conv
    _orig_is_torch_version = _conv.is_torch_version
    def _force_old_torch(op: str, ver: str):
        if op == ">=" and ver == "2.9":
            return False
        return _orig_is_torch_version(op, ver)
    _conv.is_torch_version = _force_old_torch

    # The dynamo exporter writes external-data sidecars as `<name>.onnx_data`,
    # but optimum's post-export cleanup expects `<name>.onnx.data` (legacy
    # torchscript naming). The mismatch fires a FileNotFoundError that
    # masks an otherwise successful export. Wrap os.remove in convert.py
    # to be non-fatal.
    import os as _os
    _orig_remove = _os.remove
    def _lenient_remove(path):
        try:
            return _orig_remove(path)
        except FileNotFoundError:
            log.debug("Cleanup skipped (file not present): %s", path)
            return None
    _conv.os.remove = _lenient_remove

    # Optimum runs `config.fix_dynamic_axes()` post-export to verify the
    # ONNX file loads in onnxruntime + patches dynamic shape names. With
    # the dynamo exporter on torch 2.11 some seq2seq decoder shapes
    # produce metadata that onnxruntime 1.25 rejects. We don't need that
    # post-fix for our use (we'll validate inputs ourselves later), so
    # short-circuit it to a no-op.
    from optimum.exporters.onnx import base as _base
    _base.OnnxConfig.fix_dynamic_axes = (
        lambda self, output, device="cpu", input_shapes=None, dtype="fp32": None
    )
    log.info("Patched optimum: dynamo exporter + lenient cleanup + skip post-fix.")

    IndicTransOnnxConfig = build_indictrans_onnx_config(deps)

    # Register IndicTrans as a known model type. The model_type string in
    # the model's config.json is "IndicTrans" (PascalCase). Once it's in
    # TasksManager._SUPPORTED_MODEL_TYPE, optimum's `custom_architecture`
    # check is False and the native seq2seq export path runs — which knows
    # how to split the model into encoder / decoder / decoder-with-past
    # via .with_behavior() with the right task wiring.
    # The lookup uses `model.config.model_type` literally — for IndicTrans
    # that string is "IndicTrans" (PascalCase, defined in the upstream
    # configuration_indictrans.py). Register both casings to be safe.
    log.info("Registering IndicTrans OnnxConfig with optimum's TasksManager …")
    entry = {
        "onnx": {
            "feature-extraction": IndicTransOnnxConfig,
            "text2text-generation": IndicTransOnnxConfig,
            "text2text-generation-with-past": IndicTransOnnxConfig,
        },
    }
    TasksManager._SUPPORTED_MODEL_TYPE["IndicTrans"] = entry
    TasksManager._SUPPORTED_MODEL_TYPE["indictrans"] = entry

    log.info("Exporting %s → %s (task=%s, dtype=%s, opset=%s)",
             args.model_id, out_dir, args.task, args.dtype, args.opset)

    main_export(
        model_name_or_path=args.model_id,
        output=out_dir,
        task=args.task,
        opset=args.opset,
        dtype=args.dtype,
        device="cpu",
        trust_remote_code=True,
        do_validation=not args.no_validate,
        no_dynamic_axes=args.no_dynamic_axes,
        library_name="transformers",
    )

    log.info("Done. Output:")
    for f in sorted(out_dir.iterdir()):
        size_mb = f.stat().st_size / (1024 * 1024)
        log.info("  %-45s %8.1f MB", f.name, size_mb)

    return 0


if __name__ == "__main__":
    sys.exit(main())
