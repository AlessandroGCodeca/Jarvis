"""Speech-to-text via ElevenLabs Scribe.

Server-side transcription for browsers whose Web Speech API is missing or
unreliable (Safari): the frontend records audio with MediaRecorder and POSTs
the blob to /stt, which lands here. Reuses the same ELEVENLABS_API_KEY as
tts_module — no extra service or key required.

Request shape (verified against the ElevenLabs SDK): POST /v1/speech-to-text
with the xi-api-key header, the audio in a multipart field named "file", and a
form field "model_id" (default scribe_v1). The transcript is the "text" field
of the JSON response. Language is left to auto-detect since JARVIS is spoken
to in several languages.
"""

import os

import httpx

_API_URL = "https://api.elevenlabs.io/v1/speech-to-text"


class SttError(Exception):
    """Transcription failed; the message is safe to show to the user."""


def stt_configured() -> bool:
    """True if the ElevenLabs key needed for transcription is set."""
    return bool(os.getenv("ELEVENLABS_API_KEY"))


async def transcribe(
    audio: bytes, mime_type: str = None, filename: str = None
) -> str:
    """Transcribe recorded audio bytes and return the transcript text.

    Accepts whatever container the browser's MediaRecorder produced (WebM/Opus
    from Chrome, MP4/AAC from Safari). Raises ``SttError`` with a clear message
    on any failure so the endpoint reports it instead of a bare 500.
    """
    api_key = os.getenv("ELEVENLABS_API_KEY")
    if not api_key:
        raise SttError(
            "Speech-to-text is not configured (ELEVENLABS_API_KEY is missing)."
        )
    if not audio:
        raise SttError("No audio received.")

    model_id = os.getenv("ELEVENLABS_STT_MODEL_ID", "scribe_v1")
    files = {
        "file": (
            filename or "audio.webm",
            audio,
            mime_type or "application/octet-stream",
        )
    }
    try:
        async with httpx.AsyncClient(timeout=60) as client:
            resp = await client.post(
                _API_URL,
                headers={"xi-api-key": api_key},
                data={"model_id": model_id},
                files=files,
            )
    except httpx.HTTPError as exc:
        raise SttError(
            f"Could not reach the transcription service: {exc}"
        ) from exc

    if resp.status_code != 200:
        # ElevenLabs errors carry {"detail": {"message": ...}} or {"detail": str}.
        try:
            detail = resp.json().get("detail")
            if isinstance(detail, dict):
                detail = detail.get("message") or str(detail)
        except Exception:  # noqa: BLE001 - non-JSON error body
            detail = resp.text[:200]
        raise SttError(
            f"Transcription failed (HTTP {resp.status_code}): "
            f"{detail or 'unknown error'}"
        )

    try:
        text = (resp.json().get("text") or "").strip()
    except Exception as exc:  # noqa: BLE001
        raise SttError(
            "Transcription service returned an unreadable response."
        ) from exc
    return text
