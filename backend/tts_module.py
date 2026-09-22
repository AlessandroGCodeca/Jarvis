"""Text-to-speech via ElevenLabs, with a macOS `say` fallback.

``text_to_speech`` returns base64-encoded MP3 audio on success. If ElevenLabs
is unconfigured or fails, it falls back to the local `say` command and returns
``None`` so the frontend can display text without audio.

For a streaming feel, ``split_sentences`` + ``synthesize_chunk`` let the server
synthesize and send one sentence at a time, so the first sentence can start
playing while the rest are still being generated.
"""

import base64
import os
import re

import httpx

_API_BASE = "https://api.elevenlabs.io/v1/text-to-speech"


def elevenlabs_configured() -> bool:
    """True if an ElevenLabs key + voice are set (cloud TTS available)."""
    return bool(os.getenv("ELEVENLABS_API_KEY") and os.getenv("ELEVENLABS_VOICE_ID"))


def split_sentences(text: str):
    """Split text into sentences for chunked synthesis.

    Splits on sentence-ending punctuation followed by whitespace, then merges
    very short fragments into the previous sentence so the audio isn't choppy.
    """
    text = (text or "").strip()
    if not text:
        return []
    parts = re.split(r"(?<=[.!?])\s+", text)
    merged: list[str] = []
    for part in parts:
        part = part.strip()
        if not part:
            continue
        if merged and len(part) < 15:
            merged[-1] = f"{merged[-1]} {part}"
        else:
            merged.append(part)
    return merged


def _say_fallback(text: str) -> None:
    """Speak locally via macOS `say`. Best-effort, never raises."""
    try:
        import subprocess

        subprocess.Popen(["say", text])
    except Exception:  # noqa: BLE001 - fallback is best-effort
        pass


# Failures are reported once per distinct reason: a bad key fails on every
# sentence of every reply, and repeating it would bury the rest of the log.
_reported_failures: set[str] = set()


def error_reason(resp) -> str:
    """Pull the human-readable message out of an ElevenLabs error body.

    Shared with the boot-time preflight (``preflight.py``), which probes the
    same API and gets errors in the same ``{"detail": ...}`` envelope.
    """
    try:
        detail = resp.json().get("detail")
        if isinstance(detail, dict):
            return detail.get("message") or detail.get("status") or resp.text[:200]
        if isinstance(detail, str):
            return detail
    except Exception:  # noqa: BLE001 - not JSON, fall back to raw text
        pass
    return (resp.text or "").strip()[:200] or "no details"


def _report_failure(reason: str) -> None:
    """Print why speech synthesis failed — once per distinct reason.

    Without this the failure is invisible: JARVIS answers in text and simply
    never speaks, with nothing in the log to say why.
    """
    if reason in _reported_failures:
        return
    _reported_failures.add(reason)
    print(f"TTS unavailable — {reason}", flush=True)


def reset_failure_reporting() -> None:
    """Forget which failures have been reported (used by the tests)."""
    _reported_failures.clear()


async def _synth_elevenlabs(text: str, language_code: str = None):
    """Synthesize ``text`` via ElevenLabs → raw MP3 bytes, or None on failure.

    ``language_code`` (e.g. 'sk', 'it', 'cs', 'en') is forwarded to the
    multilingual flash model so non-English replies are pronounced correctly.
    """
    api_key = os.getenv("ELEVENLABS_API_KEY")
    voice_id = os.getenv("ELEVENLABS_VOICE_ID")
    model_id = os.getenv("ELEVENLABS_MODEL_ID", "eleven_flash_v2_5")
    output_format = os.getenv("ELEVENLABS_OUTPUT_FORMAT", "mp3_44100_128")

    if not api_key or not voice_id:
        return None

    url = f"{_API_BASE}/{voice_id}/stream"
    headers = {
        "xi-api-key": api_key,
        "Content-Type": "application/json",
        "Accept": "audio/mpeg",
    }
    payload = {
        "text": text,
        "model_id": model_id,
        "voice_settings": {"stability": 0.5, "similarity_boost": 0.75},
    }
    if language_code:
        payload["language_code"] = language_code
    params = {"output_format": output_format}

    try:
        chunks = bytearray()
        async with httpx.AsyncClient(timeout=30) as client:
            async with client.stream(
                "POST", url, headers=headers, params=params, json=payload
            ) as resp:
                if resp.status_code >= 400:
                    # Read the streamed body so the reason is available; a
                    # streaming response has no .text until it is consumed.
                    await resp.aread()
                    _report_failure(
                        f"HTTP {resp.status_code}: {error_reason(resp)}"
                    )
                    return None
                async for chunk in resp.aiter_bytes():
                    if chunk:
                        chunks.extend(chunk)
        if not chunks:
            _report_failure("the service returned no audio")
            return None
        return bytes(chunks)
    except Exception as exc:  # noqa: BLE001 - caller decides how to fall back
        _report_failure(f"{type(exc).__name__}: {exc}")
        return None


async def synthesize_chunk(text: str, language_code: str = None):
    """Synthesize one sentence → base64 MP3, or None if it can't be synthesized.

    No local fallback here: chunked playback is only used when ElevenLabs is
    configured, and the caller handles the no-audio case.
    """
    if not text or not text.strip():
        return None
    audio = await _synth_elevenlabs(text, language_code)
    if audio is None:
        return None
    return base64.b64encode(audio).decode("ascii")


async def text_to_speech(text: str, language_code: str = None):
    """Return base64 MP3 from ElevenLabs, or None (and speak via `say`)."""
    if not text or not text.strip():
        return None

    audio = await _synth_elevenlabs(text, language_code)
    if audio is None:
        _say_fallback(text)
        return None
    return base64.b64encode(audio).decode("ascii")
