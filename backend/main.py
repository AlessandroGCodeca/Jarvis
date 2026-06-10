"""JARVIS FastAPI server.

Exposes a WebSocket at /ws that accepts JSON text messages, routes them
through the Claude brain (with tool calling), synthesizes speech via
ElevenLabs, and returns text + base64 audio.
"""

import asyncio
from contextlib import asynccontextmanager

from dotenv import load_dotenv
from fastapi import FastAPI, WebSocket, WebSocketDisconnect
from fastapi.middleware.cors import CORSMiddleware

import memory
import tts_module
from claude_client import JarvisBrain

load_dotenv()


@asynccontextmanager
async def lifespan(app: FastAPI):
    # Startup: ensure the memory store exists.
    memory.init_db()
    yield
    # Shutdown: nothing to clean up.


app = FastAPI(title="JARVIS", lifespan=lifespan)

app.add_middleware(
    CORSMiddleware,
    allow_origins=[
        "http://localhost:5173",
        "http://127.0.0.1:5173",
    ],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


@app.get("/")
def health():
    return {"status": "ok", "service": "JARVIS"}


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
        notifier_task.cancel()
        for task in list(timer_tasks):
            task.cancel()
