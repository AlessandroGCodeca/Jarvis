"""JARVIS FastAPI server.

Exposes a WebSocket at /ws that accepts JSON text messages, routes them
through the Claude brain (with tool calling), synthesizes speech via
ElevenLabs, and returns text + base64 audio.
"""

import asyncio

from dotenv import load_dotenv
from fastapi import FastAPI, WebSocket, WebSocketDisconnect
from fastapi.middleware.cors import CORSMiddleware

import memory
import tts_module
from claude_client import JarvisBrain

load_dotenv()

app = FastAPI(title="JARVIS")

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


@app.on_event("startup")
def _startup() -> None:
    memory.init_db()


@app.get("/")
def health():
    return {"status": "ok", "service": "JARVIS"}


@app.websocket("/ws")
async def websocket_endpoint(websocket: WebSocket):
    await websocket.accept()
    brain = JarvisBrain()  # per-connection conversation history
    try:
        while True:
            data = await websocket.receive_json()
            msg_type = data.get("type")

            if msg_type == "ping":
                await websocket.send_json({"type": "pong"})
                continue

            if msg_type == "message":
                content = (data.get("content") or "").strip()
                if not content:
                    continue

                # Run the (blocking) Claude tool loop off the event loop.
                reply = await asyncio.to_thread(brain.process, content)

                # Synthesize speech (returns None on fallback / failure).
                audio = await tts_module.text_to_speech(reply)

                await websocket.send_json(
                    {"type": "response", "text": reply, "audio": audio}
                )
                continue

            # Unknown message type — ignore quietly.
    except WebSocketDisconnect:
        return
    except Exception as exc:  # noqa: BLE001 - report and close cleanly
        try:
            await websocket.send_json({"type": "error", "message": str(exc)})
        except Exception:  # noqa: BLE001
            pass
