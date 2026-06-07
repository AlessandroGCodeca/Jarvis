"""Text-to-speech via ElevenLabs, with a macOS `say` fallback.

``text_to_speech`` returns base64-encoded MP3 audio on success. If ElevenLabs
is unconfigured or fails, it falls back to the local `say` command and returns
``None`` so the frontend can display text without audio.
"""

import asyncio
import base64
import os

import httpx

_API_BASE = "https://api.elevenlabs.io/v1/text-to-speech"


def _say_fallback(text: str) -> None:
    """Speak locally via macOS `say`. Best-effort, never raises."""
    try:
        import subprocess

        subprocess.Popen(["say", text])
    except Exception:  # noqa: BLE001 - fallback is best-effort
        pass


async def text_to_speech(text: str):
    """Return base64 MP3 from ElevenLabs, or None (and speak via `say`)."""
    if not text or not text.strip():
        return None

    api_key = os.getenv("ELEVENLABS_API_KEY")
    voice_id = os.getenv("ELEVENLABS_VOICE_ID")
    model_id = os.getenv("ELEVENLABS_MODEL_ID", "eleven_flash_v2_5")
    output_format = os.getenv("ELEVENLABS_OUTPUT_FORMAT", "mp3_44100_128")

    if not api_key or not voice_id:
        _say_fallback(text)
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
        if not chunks:
            _say_fallback(text)
            return None
        return base64.b64encode(bytes(chunks)).decode("ascii")
    except Exception:  # noqa: BLE001 - fall back to local TTS
        _say_fallback(text)
        return None
