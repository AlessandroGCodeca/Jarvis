"""JARVIS FastAPI server.

Exposes a WebSocket at /ws that accepts JSON text messages, routes them
through the Claude brain (with tool calling), synthesizes speech via
ElevenLabs, and returns text + base64 audio.
"""

import asyncio
import socket
import time
from collections import deque
from contextlib import asynccontextmanager

from dotenv import load_dotenv
from fastapi import FastAPI, Request, WebSocket, WebSocketDisconnect
from fastapi.middleware.cors import CORSMiddleware

import habits_module
import memory
import offline_module
import tasks_module
import tts_module
from claude_client import JarvisBrain
from notifications_module import NotificationEngine

load_dotenv()

# Per-connection notification sinks (each enqueues text onto that connection's
# spoken-notification queue) + last user-interaction time for idle gating.
_sinks: set = set()
_last_interaction = time.time()

# Shared brain + simple rate limiter for the /voice HTTP endpoint.
_http_brain: JarvisBrain | None = None
_voice_calls: deque = deque()  # timestamps of recent /voice requests
_VOICE_RATE_LIMIT = 10  # max requests per minute


def _local_ip() -> str:
    """Best-effort local IP for the iPhone companion URL."""
    try:
        return socket.gethostbyname(socket.gethostname())
    except Exception:  # noqa: BLE001
        return "127.0.0.1"


def _broadcast(text: str, priority: str = "normal") -> None:
    """Deliver a proactive spoken notification to all connected clients.

    Routed through each connection's existing spoken-notification queue (the
    same path timers use), so no frontend changes are needed.
    """
    for sink in list(_sinks):
        try:
            sink(text)
        except Exception:  # noqa: BLE001 - a dead connection shouldn't break others
            pass


async def _connectivity_loop() -> None:
    """Refresh the online/offline flag at startup and every 5 minutes."""
    while True:
        try:
            await offline_module.check_connectivity()
        except Exception:  # noqa: BLE001 - never crash on a probe failure
            pass
        await asyncio.sleep(300)


@asynccontextmanager
async def lifespan(app: FastAPI):
    # Startup: ensure the stores exist, do an initial connectivity probe, and
    # start the background connectivity + proactive-notification tasks.
    memory.init_db()
    tasks_module.init_tasks_table()
    habits_module.init_habits_tables()
    try:
        await offline_module.check_connectivity()
    except Exception:  # noqa: BLE001
        pass

    print(f"📱 iPhone companion URL: http://{_local_ip()}:8000/voice")

    conn_task = asyncio.ensure_future(_connectivity_loop())
    engine = NotificationEngine(
        _broadcast, idle_seconds=lambda: time.time() - _last_interaction
    )
    engine.start()
    try:
        yield
    finally:
        # Shutdown: stop background tasks cleanly.
        await engine.stop()
        conn_task.cancel()


app = FastAPI(title="JARVIS", lifespan=lifespan)

app.add_middleware(
    CORSMiddleware,
    allow_origins=[
        "http://localhost:5173",
        "http://127.0.0.1:5173",
    ],
    # Also allow requests from the local network (e.g. an iPhone Shortcut on
    # the same Wi-Fi) — 192.168.x.x and 10.x.x.x on any port.
    allow_origin_regex=r"http://(192\.168\.\d{1,3}\.\d{1,3}|10\.\d{1,3}\.\d{1,3}\.\d{1,3})(:\d+)?",
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


@app.get("/")
def health():
    return {
        "status": "ok",
        "service": "JARVIS",
        "online": not offline_module.is_offline(),
        "connectivity": offline_module.status_label(),
    }


def _voice_rate_limited() -> bool:
    """True if the /voice endpoint has exceeded its per-minute budget."""
    now = time.time()
    while _voice_calls and now - _voice_calls[0] > 60:
        _voice_calls.popleft()
    if len(_voice_calls) >= _VOICE_RATE_LIMIT:
        return True
    _voice_calls.append(now)
    return False


@app.post("/voice")
async def voice_endpoint(request: Request):
    """HTTP entry point for the iPhone Shortcut companion.

    Accepts {"text": "..."}, runs it through the same brain pipeline, and
    returns {"response": text, "audio": base64-mp3-or-null}. Rate-limited to
    ten requests per minute.
    """
    if _voice_rate_limited():
        return {"error": "rate limit exceeded — max 10 requests per minute"}
    try:
        body = await request.json()
    except Exception:  # noqa: BLE001
        return {"error": "invalid JSON body"}
    text = (body.get("text") or "").strip()
    if not text:
        return {"error": "no text provided"}

    global _http_brain
    if _http_brain is None:
        _http_brain = JarvisBrain()

    reply = await asyncio.to_thread(_http_brain.process, text)
    audio = await tts_module.text_to_speech(
        reply, getattr(_http_brain, "language", None)
    )
    return {"response": reply, "audio": audio}


@app.websocket("/ws")
async def websocket_endpoint(websocket: WebSocket):
    await websocket.accept()

    loop = asyncio.get_running_loop()
    # Queue of pending spoken notifications (timer / pomodoro / focus alerts).
    notif_queue: asyncio.Queue = asyncio.Queue()
    # Live delayed-notification tasks, cancelled on disconnect.
    timer_tasks: set = set()
    # Serialize all sends so a notification can't interleave with a reply's
    # audio stream (WebSocket sends are not safe to call concurrently).
    send_lock = asyncio.Lock()

    def schedule_notification(delay_seconds: float, text: str) -> None:
        """Arm a spoken notification after ``delay_seconds``.

        Thread-safe: the Claude tool loop runs in a worker thread, so timers are
        armed onto the event loop via ``call_soon_threadsafe``.
        """

        def _arm() -> None:
            async def _fire() -> None:
                try:
                    await asyncio.sleep(max(0.0, delay_seconds))
                    await notif_queue.put(text)
                except asyncio.CancelledError:
                    pass

            task = loop.create_task(_fire())
            timer_tasks.add(task)
            task.add_done_callback(timer_tasks.discard)

        loop.call_soon_threadsafe(_arm)

    brain = JarvisBrain(notifier=schedule_notification)

    # Register a sink so the proactive notification engine can broadcast spoken
    # messages to this connection (enqueued onto the same path timers use).
    def _sink(text: str) -> None:
        notif_queue.put_nowait(text)

    _sinks.add(_sink)

    async def speak(text: str, language_code: str = None) -> None:
        """Send text + streamed speech for one turn, serialized via send_lock."""
        async with send_lock:
            # Send the text immediately so the UI reacts without waiting for TTS.
            await websocket.send_json({"type": "response", "text": text})

            # Stream speech sentence-by-sentence so the first sentence can start
            # playing while later ones are still being synthesized.
            if tts_module.elevenlabs_configured():
                sentences = tts_module.split_sentences(text) or [text]
                for sentence in sentences:
                    audio = await tts_module.synthesize_chunk(
                        sentence, language_code
                    )
                    await websocket.send_json({"type": "audio", "audio": audio})
            else:
                # No cloud TTS configured: speak the whole reply locally via
                # macOS `say` (returns None, no audio for the browser).
                await tts_module.text_to_speech(text, language_code)

            # Signal the end of this turn's audio stream.
            await websocket.send_json({"type": "audio_end"})

    async def receive_loop() -> None:
        """Handle inbound messages until the socket closes."""
        while True:
            data = await websocket.receive_json()
            msg_type = data.get("type")

            if msg_type == "ping":
                async with send_lock:
                    await websocket.send_json({"type": "pong"})
                continue

            if msg_type == "message":
                content = (data.get("content") or "").strip()
                if not content:
                    continue
                global _last_interaction
                _last_interaction = time.time()
                # Run the (blocking) Claude tool loop off the event loop.
                reply = await asyncio.to_thread(brain.process, content)
                # Speak the reply in the language JARVIS detected for this turn.
                await speak(reply, getattr(brain, "language", None))
                continue

            # Unknown message type — ignore quietly.

    async def notification_loop() -> None:
        """Speak notifications (e.g. a finished timer) as they come due."""
        while True:
            text = await notif_queue.get()
            try:
                await speak(text)
            except Exception:  # noqa: BLE001 - socket likely closing
                return

    notifier_task = asyncio.ensure_future(notification_loop())
    try:
        await receive_loop()
    except WebSocketDisconnect:
        return
    except Exception as exc:  # noqa: BLE001 - report and close cleanly
        try:
            async with send_lock:
                await websocket.send_json(
                    {"type": "error", "message": str(exc)}
                )
        except Exception:  # noqa: BLE001
            pass
    finally:
        _sinks.discard(_sink)
        notifier_task.cancel()
        for task in list(timer_tasks):
            task.cancel()
