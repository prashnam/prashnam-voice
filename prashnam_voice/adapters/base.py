"""Adapter contracts — Translator and TTS interfaces, plus the small value
objects an adapter exposes for setup forms (Setting) and voice browsing
(Voice).

Adapters are the swap point between local-running models (the AI4Bharat
stack) and cloud services (Sarvam, ElevenLabs, Google Cloud TTS, OpenAI,
Bhashini). The rest of prashnam-voice talks only to these protocols, not
to a concrete model class.

A *configured* adapter takes a `dict` of per-instance settings on every
call (typically API keys, region, account id). The framework persists
those settings; the adapter is otherwise stateless across calls.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Protocol, runtime_checkable


@dataclass(frozen=True)
class Setting:
    """A single piece of configuration an adapter needs from the user.

    Drives the onboarding wizard's setup form: each Setting renders as a
    labelled input. Use `url` to point at the provider's signup / API-key
    page so the wizard can offer a one-click 'Open in browser'.
    """

    key: str                          # machine name, e.g. "api_key"
    label: str                        # human label, e.g. "API key"
    help: str = ""                    # one-line hint shown under the field
    secret: bool = True               # render masked + omit from logs
    url: str | None = None            # link to the provider's docs / dashboard
    required: bool = True
    placeholder: str | None = None


@dataclass(frozen=True)
class Voice:
    """A speaker available from a TTS adapter, scoped to one language."""

    id: str                           # adapter-internal id
    name: str                         # human label
    lang: str                         # FLORES code or our internal lang code
    gender: str | None = None
    sample_url: str | None = None     # optional preview clip


# ---------------------------------------------------------------------------
# Protocols
# ---------------------------------------------------------------------------


@runtime_checkable
class TranslatorAdapter(Protocol):
    """Translates English source text into one Indic language at a time.

    Implementations:
      - load any heavy state lazily (the adapter object may exist long
        before it's used)
      - are responsible for raising a clear exception if `is_configured`
        is False but a translate call comes in anyway
      - should batch within a single `translate_batch` call when the
        backend supports it; the framework already groups texts per
        language for you
    """

    name: str                         # short id, e.g. "local-ai4bharat"
    label: str                        # human label, e.g. "AI4Bharat (local)"
    description: str
    supports_offline: bool
    supported_langs: list[str]
    needs_setup: list[Setting]

    def is_configured(self, cfg: dict) -> bool: ...

    def translate_batch(
        self, texts: list[str], lang: str, cfg: dict
    ) -> list[str]: ...

    def close(self) -> None: ...


@runtime_checkable
class TTSAdapter(Protocol):
    """Synthesizes one (text, lang, voice) pair into MP3 bytes.

    The framework owns caching and file I/O. Adapters return raw bytes
    (mp3) and are not responsible for writing to disk.

    The `pace` parameter is one of the values in
    `prashnam_voice.config.PACE_PHRASES`. Adapters that don't natively
    expose pace (e.g. Sarvam — yet) should ignore it or map it to the
    closest supported control (e.g. SSML rate, prompt-description text).
    """

    name: str
    label: str
    description: str
    supports_offline: bool
    supported_langs: list[str]
    needs_setup: list[Setting]

    def is_configured(self, cfg: dict) -> bool: ...

    def voices_for(self, lang: str, cfg: dict) -> list[Voice]: ...

    def synthesize(
        self,
        text: str,
        lang: str,
        voice: str,
        pace: str,
        cfg: dict,
    ) -> bytes:
        """Return a complete MP3 file as bytes."""
        ...

    def close(self) -> None: ...


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


@dataclass
class AdapterError(Exception):
    """Raised by adapters for setup or runtime failures the user can fix
    (e.g. missing API key, quota exceeded, network down).

    Distinct from generic exceptions so the UI can surface them with a
    'check your config' affordance instead of a stack trace.
    """

    adapter: str
    message: str
    setup_required: bool = False

    def __str__(self) -> str:
        return f"[{self.adapter}] {self.message}"
