"""Boot-time credential checks.

Every key JARVIS holds is first exercised in the middle of a conversation,
and each one fails in its own quiet way: a wrong ``ELEVENLABS_API_KEY`` means
replies arrive as text and never speak, a stale ``ELEVENLABS_VOICE_ID`` does
the same with a perfectly good key, and a wrong ``ANTHROPIC_API_KEY`` turns
the first thing you say into an error. They are setup mistakes — made once,
discovered much later, and diagnosed by hand.

So each configured service is asked whether its credential actually works,
before anyone can talk to JARVIS, and the answers are printed as one line per
check:

    JARVIS preflight:
      ✓ Anthropic   key valid, model claude-haiku-4-5
      ✗ ElevenLabs  HTTP 400 — API key ID used as API key, only valid API
                    keys can be used. API keys start with 'sk_'.
      – Voice ID    not checked — see the line above
      – Spotify     not configured — "play <song>" opens a search instead

Three marks: ``✓`` the credential works, ``✗`` it doesn't (fix it), ``–``
nothing was learned — not configured, unreachable, or not worth asking about
because an earlier check already found the cause.

Every probe is free (no tokens, no characters spent), they run concurrently,
and each is capped at ``CHECK_TIMEOUT`` so a wedged service can't hold the
boot. Nothing here aborts startup: a ``✗`` is information, and JARVIS goes on
to run exactly as degraded as it would have anyway. Services that need no
credential (weather, news, Wikipedia) have nothing to get wrong and aren't
probed. Set ``JARVIS_SKIP_PREFLIGHT=1`` to skip the checks entirely.
"""

import asyncio
import base64
import os
from dataclasses import dataclass

import anthropic
import httpx

import claude_client
import offline_module
import tts_module

# Per-check ceiling. Generous enough for a slow-but-working service, short
# enough that a wedged one can't turn a boot into a wait.
CHECK_TIMEOUT = 6.0
# Below it, so a slow response is reported as a timeout by the HTTP client
# (with its reason) rather than by the blunter per-check guard.
_HTTP_TIMEOUT = 5.0

_ELEVENLABS_API = "https://api.elevenlabs.io/v1"
_SPOTIFY_TOKEN_URL = "https://accounts.spotify.com/api/token"

OK = "ok"
FAIL = "fail"
UNKNOWN = "unknown"

_MARKS = {OK: "✓", FAIL: "✗", UNKNOWN: "–"}


@dataclass(frozen=True)
class Check:
    """One credential check: what was checked, how it went, and why."""

    label: str
    status: str
    detail: str


# --- HTTP plumbing ----------------------------------------------------------


async def _fetch(method: str, url: str, **kwargs):
    """Make one request. Returns ``(response, exception)``; never raises."""
    try:
        async with httpx.AsyncClient(timeout=_HTTP_TIMEOUT) as client:
            return await client.request(method, url, **kwargs), None
    except Exception as exc:  # noqa: BLE001 - a failed probe is a result too
        return None, exc


def _describe(exc: BaseException) -> str:
    """Name an exception the way a log reader needs it."""
    detail = str(exc).strip()
    return f"{type(exc).__name__}: {detail}" if detail else type(exc).__name__


def _http_problem(label: str, resp, exc, reason) -> Check | None:
    """The ``Check`` for a probe that didn't come back 200, else None.

    A 4xx is the credential being rejected — that's the answer we came for.
    A transport error, a 5xx, or a 404 (the request went somewhere that isn't
    there, which is this code's problem and not the user's) says nothing about
    the key, so it reports as "couldn't check" rather than accusing a key that
    may be perfectly fine. Nobody should be sent to re-issue a working key
    because a vendor moved an endpoint.
    """
    if exc is not None:
        return Check(label, UNKNOWN, f"couldn't check — {_describe(exc)}")
    if resp.status_code == 404 or resp.status_code >= 500:
        return Check(
            label,
            UNKNOWN,
            f"couldn't check — the service returned HTTP {resp.status_code}",
        )
    if resp.status_code != 200:
        return Check(label, FAIL, f"HTTP {resp.status_code} — {reason(resp)}")
    return None


# --- Anthropic --------------------------------------------------------------


def _new_anthropic_client():
    """The client ``JarvisBrain`` uses, minus the retries.

    Configured from the environment exactly as the brain's is, so what gets
    validated is what will actually do the talking. Retries are off because a
    boot check should report a refusal, not sit patiently re-asking.
    """
    return anthropic.AsyncAnthropic(max_retries=0, timeout=_HTTP_TIMEOUT)


def _anthropic_reason(exc) -> str:
    """The human half of an Anthropic error.

    The SDK's own message is ``Error code: 401 - {'type': 'error', ...}`` —
    the dict repr, braces and all. The sentence lives in the body.
    """
    body = getattr(exc, "body", None)
    if isinstance(body, dict):
        error = body.get("error")
        if isinstance(error, dict) and error.get("message"):
            return str(error["message"])
    return str(getattr(exc, "message", "") or exc)


async def _close(client) -> None:
    """Release a client's connection pool. Best-effort, never raises."""
    try:
        await client.close()
    except Exception:  # noqa: BLE001 - nothing useful to do about it here
        pass


async def check_anthropic() -> Check:
    """Validate ``ANTHROPIC_API_KEY`` and ``CLAUDE_MODEL`` together.

    Retrieving the configured model costs nothing and answers both questions
    in one request: a bad key is a 401, and a ``CLAUDE_MODEL`` that doesn't
    exist (or that this key can't reach) is a 404. Today both of those surface
    as a failed reply to whatever you first said.
    """
    label = "Anthropic"
    if not os.getenv("ANTHROPIC_API_KEY"):
        return Check(
            label, FAIL, "ANTHROPIC_API_KEY is not set — JARVIS can't answer"
        )

    model = claude_client.MODEL
    try:
        client = _new_anthropic_client()
    except Exception as exc:  # noqa: BLE001 - e.g. an unusable auth config
        return Check(label, UNKNOWN, f"couldn't check — {_describe(exc)}")

    try:
        info = await client.models.retrieve(model)
    except anthropic.AuthenticationError as exc:
        return Check(label, FAIL, f"HTTP 401 — {_anthropic_reason(exc)}")
    except anthropic.NotFoundError:
        return Check(
            label,
            FAIL,
            f"key valid, but CLAUDE_MODEL '{model}' is not a model it can use",
        )
    except anthropic.APIConnectionError as exc:
        return Check(label, UNKNOWN, f"couldn't check — {_describe(exc)}")
    except anthropic.APIStatusError as exc:
        status = exc.status_code
        if status >= 500:
            return Check(label, UNKNOWN, f"couldn't check — HTTP {status}")
        return Check(label, FAIL, f"HTTP {status} — {_anthropic_reason(exc)}")
    except Exception as exc:  # noqa: BLE001 - an unknown failure is not a ✗
        return Check(label, UNKNOWN, f"couldn't check — {_describe(exc)}")
    finally:
        await _close(client)

    # An alias (claude-haiku-4-5) resolves to a dated snapshot; showing both
    # makes it obvious which build a reply came from.
    resolved = getattr(info, "id", "") or model
    detail = f"key valid, model {model}"
    if resolved != model:
        detail += f" → {resolved}"
    return Check(label, OK, detail)


# --- ElevenLabs -------------------------------------------------------------


def _quota(resp) -> str:
    """The used/allowed character counts, as a phrase to append. May be "".

    Worth a glance at boot: an exhausted quota is a 429 at the first spoken
    reply, which looks exactly like a broken key from the outside.
    """
    try:
        body = resp.json()
        used = int(body["character_count"])
        limit = int(body["character_limit"])
    except Exception:  # noqa: BLE001 - the key is valid either way
        return ""
    return f", {used:,} of {limit:,} characters used"


async def _check_elevenlabs_key(api_key: str) -> Check:
    """Ask ElevenLabs whether the key is real, and what's left on it."""
    resp, exc = await _fetch(
        "GET",
        f"{_ELEVENLABS_API}/user/subscription",
        headers={"xi-api-key": api_key},
    )
    # ElevenLabs wraps its errors in {"detail": ...}; tts_module already
    # knows how to unwrap them, and this is the same API.
    problem = _http_problem("ElevenLabs", resp, exc, tts_module.error_reason)
    if problem:
        return problem
    return Check("ElevenLabs", OK, f"key valid{_quota(resp)}")


async def _check_voice(api_key: str, voice_id: str) -> Check:
    """Resolve ``ELEVENLABS_VOICE_ID`` to the voice it names."""
    label = "Voice ID"
    resp, exc = await _fetch(
        "GET",
        f"{_ELEVENLABS_API}/voices/{voice_id}",
        headers={"xi-api-key": api_key},
    )
    if resp is not None and resp.status_code == 404:
        # ElevenLabs just says "voice not found" — say which one.
        return Check(
            label, FAIL, f"no voice with ID {voice_id} on this account"
        )
    problem = _http_problem(label, resp, exc, tts_module.error_reason)
    if problem:
        return problem

    try:
        name = (resp.json().get("name") or "").strip()
    except Exception:  # noqa: BLE001 - it resolved, that's the point
        name = ""
    short = voice_id if len(voice_id) <= 8 else f"{voice_id[:8]}..."
    if name:
        return Check(label, OK, f'{short} resolves to "{name}"')
    return Check(label, OK, f"{short} resolves")


async def check_elevenlabs() -> list[Check]:
    """Validate the ElevenLabs key and voice ID — two rows, two failures.

    They break independently: a valid key with a stale voice ID is silent in
    exactly the same way as a bad key, and each has its own fix. The voice
    lookup is skipped when the key has already been shown to be the problem,
    so one root cause produces one ``✗``.
    """
    api_key = os.getenv("ELEVENLABS_API_KEY")
    voice_id = os.getenv("ELEVENLABS_VOICE_ID")

    if not api_key and not voice_id:
        # The documented degraded mode, not a mistake: JARVIS speaks through
        # the macOS `say` command instead.
        return [
            Check(
                "ElevenLabs",
                UNKNOWN,
                "not configured — speech falls back to macOS `say`",
            )
        ]
    if not api_key:
        return [
            Check(
                "ElevenLabs",
                FAIL,
                "ELEVENLABS_API_KEY is not set, but a voice ID is — cloud "
                "speech stays off",
            )
        ]

    key_check = await _check_elevenlabs_key(api_key)
    checks = [key_check]
    if not voice_id:
        checks.append(
            Check(
                "Voice ID",
                FAIL,
                "ELEVENLABS_VOICE_ID is not set — cloud speech stays off "
                "even with a valid key",
            )
        )
    elif key_check.status != OK:
        checks.append(
            Check("Voice ID", UNKNOWN, "not checked — see the line above")
        )
    else:
        checks.append(await _check_voice(api_key, voice_id))
    return checks


# --- Spotify ----------------------------------------------------------------


def _spotify_reason(resp) -> str:
    """Spotify's OAuth errors: {"error": ..., "error_description": ...}."""
    try:
        body = resp.json()
        message = body.get("error_description") or body.get("error") or ""
        return str(message).strip() or "no details"
    except Exception:  # noqa: BLE001 - not JSON, fall back to the raw body
        return (resp.text or "").strip()[:200] or "no details"


async def check_spotify() -> Check:
    """Validate the Spotify client credentials, if they're configured.

    Optional: without them "play <song>" still opens a search and resumes
    playback. Half-configured is worth a ``✗`` though — it reads as set up
    and behaves as if it isn't.
    """
    label = "Spotify"
    client_id = os.getenv("SPOTIFY_CLIENT_ID")
    client_secret = os.getenv("SPOTIFY_CLIENT_SECRET")

    if not client_id and not client_secret:
        return Check(
            label,
            UNKNOWN,
            'not configured — "play <song>" opens a search instead',
        )
    if not client_id or not client_secret:
        missing = (
            "SPOTIFY_CLIENT_ID" if not client_id else "SPOTIFY_CLIENT_SECRET"
        )
        return Check(
            label, FAIL, f"{missing} is missing — catalog search needs both"
        )

    auth = base64.b64encode(f"{client_id}:{client_secret}".encode()).decode()
    resp, exc = await _fetch(
        "POST",
        _SPOTIFY_TOKEN_URL,
        data={"grant_type": "client_credentials"},
        headers={"Authorization": f"Basic {auth}"},
    )
    problem = _http_problem(label, resp, exc, _spotify_reason)
    if problem:
        return problem
    return Check(label, OK, "client credentials accepted")


# --- running and reporting --------------------------------------------------


async def _bounded(label: str, coro) -> list[Check]:
    """Run one check under the timeout; turn anything it throws into a row."""
    try:
        result = await asyncio.wait_for(coro, CHECK_TIMEOUT)
    except asyncio.TimeoutError:
        return [Check(label, UNKNOWN, f"timed out after {CHECK_TIMEOUT:.0f}s")]
    except Exception as exc:  # noqa: BLE001 - a bug here can't block the boot
        return [Check(label, UNKNOWN, f"couldn't check — {_describe(exc)}")]
    return result if isinstance(result, list) else [result]


async def run_checks() -> list[Check]:
    """Run every check concurrently. Returns the rows in display order."""
    groups = await asyncio.gather(
        _bounded("Anthropic", check_anthropic()),
        _bounded("ElevenLabs", check_elevenlabs()),
        _bounded("Spotify", check_spotify()),
    )
    return [check for group in groups for check in group]


def format_report(checks: list[Check]) -> list[str]:
    """Render the checks as the aligned block printed at boot."""
    if not checks:
        return []
    width = max(len(check.label) for check in checks)
    lines = ["JARVIS preflight:"]
    for check in checks:
        mark = _MARKS.get(check.status, "?")
        lines.append(f"  {mark} {check.label.ljust(width)}  {check.detail}")
    failed = sum(1 for check in checks if check.status == FAIL)
    if failed:
        plural = "" if failed == 1 else "s"
        lines.append(
            f"  {failed} check{plural} failed — fix backend/.env. JARVIS "
            "starts anyway, degraded."
        )
    return lines


async def run() -> list[Check]:
    """Run the preflight and print its report. Never raises.

    Returns the checks so a caller (or a test) can inspect them; the point of
    the call is the printing.
    """
    if os.getenv("JARVIS_SKIP_PREFLIGHT"):
        return []
    if offline_module.is_offline():
        # Offline every probe would fail for the same uninteresting reason.
        print("JARVIS preflight: skipped — no internet connection.", flush=True)
        return []

    try:
        checks = await run_checks()
    except Exception as exc:  # noqa: BLE001 - report it, don't block the boot
        print(f"JARVIS preflight: couldn't run — {_describe(exc)}", flush=True)
        return []

    for line in format_report(checks):
        print(line, flush=True)
    return checks
