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


async def _synth_elevenlabs(text: str):
    """Synthesize ``text`` via ElevenLabs → raw MP3 bytes, or None on failure."""
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
    params = {"output_format": output_format}

    try:
        chunks = bytearray()
        async with httpx.AsyncClient(timeout=30) as client:
            async with client.stream(
                "POST", url, headers=headers, params=params, json=payload
            ) as resp:
                resp.raise_for_status()
                async for chunk in resp.aiter_bytes():
                    if chunk:
                        chunks.extend(chunk)
        return bytes(chunks) if chunks else None
    except Exception:  # noqa: BLE001 - caller decides how to fall back
        return None


async def synthesize_chunk(text: str):
    """Synthesize one sentence → base64 MP3, or None if it can't be synthesized.

    No local fallback here: chunked playback is only used when ElevenLabs is
    configured, and the caller handles the no-audio case.
    """
    if not text or not text.strip():
        return None
    audio = await _synth_elevenlabs(text)
    if audio is None:
        return None
    return base64.b64encode(audio).decode("ascii")


async def text_to_speech(text: str):
    """Return base64 MP3 from ElevenLabs, or None (and speak via `say`)."""
    if not text or not text.strip():
        return None

    audio = await _synth_elevenlabs(text)
    if audio is None:
        _say_fallback(text)
        return None
    return base64.b64encode(audio).decode("ascii")
