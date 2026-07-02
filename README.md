# JARVIS — a voice-first AI assistant for macOS

A voice-first assistant with an audio-reactive particle orb. Speak to it in your
browser; it thinks with Claude, talks back with ElevenLabs, and can drive your
Mac's Calendar, Mail, Notes, Terminal, and the web.

```
Microphone → Web Speech API → WebSocket → FastAPI → Claude (Haiku)
          → ElevenLabs TTS → WebSocket → Browser speaker
```

- **Backend:** Python + FastAPI, SQLite (FTS5) memory, AppleScript bridges,
  Claude tool-calling, ElevenLabs TTS (with a local `say` fallback).
- **Frontend:** Vite + TypeScript (vanilla), a Three.js audio-reactive orb,
  Web Speech API input, auto-reconnecting WebSocket client.

---

## Prerequisites

- **Python 3.11–3.13** (3.12 recommended — `backend/.python-version` pins it
  for pyenv/uv). Bleeding-edge releases work but are the road less traveled:
  tooling and prebuilt wheels lag behind them, and the first cold import of the
  dependency tree can be dramatically slower (see Troubleshooting).
- **Node 18+**
- **macOS** — required for the AppleScript bridges (Calendar, Mail, Notes) and
  the `say` TTS fallback. The web search, terminal, and Claude features work on
  any OS; the macOS-only bridges fail gracefully elsewhere.
- API keys: an [Anthropic](https://console.anthropic.com/) key and (optionally)
  an [ElevenLabs](https://elevenlabs.io/) key + voice ID. Without ElevenLabs,
  JARVIS falls back to the macOS `say` command and the UI shows text only.

---

## Setup

### 1. Backend

```bash
cd backend
./setup.sh                    # creates venv/, installs deps, precompiles + warms up imports
cp .env.example .env          # then edit .env and fill in your keys
```

`setup.sh` picks python3.12 (falling back to 3.13/3.11; override with
`PYTHON=python3.x ./setup.sh`), recreates `venv/`, installs
`requirements.txt`, precompiles bytecode, and does one warm-up import so the
server's first boot is fast. Prefer doing it by hand? The equivalent is:

```bash
cd backend
python3.12 -m venv venv
venv/bin/pip install -r requirements.txt
venv/bin/python -m compileall -q venv/lib .
```

`.env`:

```
ANTHROPIC_API_KEY=sk-ant-...
ELEVENLABS_API_KEY=...
ELEVENLABS_VOICE_ID=...
# optional overrides:
# CLAUDE_MODEL=claude-haiku-4-5
# ELEVENLABS_MODEL_ID=eleven_multilingual_v2
# ELEVENLABS_OUTPUT_FORMAT=mp3_44100_128
```

### 2. Run the backend

```bash
cd backend
venv/bin/uvicorn main:app --reload --port 8000
```

You should see `JARVIS backend: loading...` immediately, then uvicorn's
startup lines once it binds. Health check: `curl localhost:8000` returns
`{"status":"ok","service":"JARVIS"}`.

### 3. Run the frontend

```bash
cd frontend
npm install
npm run dev
```

### 4. Use it

Open **http://localhost:5173**, click the mic button, and speak. Toggle the
conversation log with the **L** key; open **Settings** (top-left) to store keys
in your browser.

---

## AppleScript permissions

The Calendar / Mail / Notes / Terminal bridges run via `osascript`. macOS will
prompt for automation permission the first time. If a bridge silently returns
"unavailable", grant access under:

**System Settings → Privacy & Security → Automation** → allow your terminal (or
the process running `uvicorn`) to control **Calendar**, **Mail**, and **Notes**.

---

## How it works

- `main.py` — FastAPI app + `/ws` WebSocket. Accepts `{type:"message", content}`
  and `{type:"ping"}`; replies with `{type:"response", text, audio}` /
  `{type:"pong"}`. CORS is enabled for `localhost:5173`.
- `claude_client.py` — `JarvisBrain` keeps the last ~20 messages and runs a
  synchronous tool-use loop. Tools: `get_calendar`, `get_emails`, `create_note`,
  `search_web`, `run_command`. Model defaults to `claude-haiku-4-5` (override
  with `CLAUDE_MODEL`).
- `tts_module.py` — ElevenLabs streaming TTS → base64 MP3; falls back to `say`
  and returns `None` (frontend shows text only).
- `memory.py` — SQLite FTS5 store: `save_memory`, `search_memory`, `get_recent`.
- `calendar_module.py` / `mail_module.py` / `notes_module.py` — AppleScript
  bridges, each wrapped to fail gracefully.
- `browser_module.py` — DuckDuckGo HTML search + stdlib HTML-to-text fetch
  (no extra dependencies).
- `system_actions.py` — `run_command` (10s timeout) and `open_app`.

Frontend (`frontend/src/`):

- `orb.ts` — `OrbVisualizer`: ~2000-particle fibonacci sphere, additive
  blending, four states (idle / listening / thinking / speaking) with lerped
  transitions, and a shared `AudioContext` + `AnalyserNode` for reactivity.
- `voice.ts` — `VoiceInput`: Web Speech API with interim + final transcripts.
- `websocket.ts` — `WSClient`: auto-reconnect (exp. backoff), decodes base64
  audio into the orb's AudioContext, drives orb state across the message flow.
- `ui.ts` — `UI`: mic button, status, transcript, conversation log, settings.
- `main.ts` — wires it all together.

---

## Troubleshooting

### Backend starts but never binds port 8000 (silent uvicorn)

The very first start after (re)installing dependencies can sit for a long
time — minutes on some Macs — before printing anything past
`JARVIS backend: loading...`. Nothing is wrong with the app; Python is
compiling bytecode for thousands of freshly installed files and, on macOS,
Gatekeeper is scanning each new file on first read. It is a one-time cost:
warm imports are near-instant.

- **Avoid it up front:** run `backend/setup.sh` — it precompiles bytecode and
  performs a warm-up import at install time, so the first real boot binds in
  seconds.
- **Already stuck?** Either wait it out once, or kill it and run
  `venv/bin/python -m compileall -q venv/lib .` followed by one throwaway
  `PYTHONPATH="$(pwd)" venv/bin/python -c "import main"`, then start uvicorn.
- **Measure it:** `time PYTHONPATH="$(pwd)" venv/bin/python -c "import main"`
  — expect ~1–2 s warm. If it's slow *every* time (not just the first), the
  venv likely fell back to source builds or pure-Python code paths; recreate
  it on Python 3.12 with `./setup.sh`.

### White page / dev-server assets returning 504

A `npm run dev` (Vite) process left running for days or weeks can wedge: `/`
still returns 200 from memory, but transformed assets like `/src/main.ts`
time out with 504 because the transform/HMR pipeline has stalled (stale
esbuild worker, exhausted file watchers, sockets broken across sleep/wake).
Restart it — `Ctrl+C`, then `npm run dev`. If it recurs, clear the cache:
`rm -rf node_modules/.vite`. Dev servers aren't built to run for weeks;
restart them with your work session.

---

## Notes & limitations

- Localhost only; no auth, single user — by design (MVP).
- Web Speech API recognition works best in Chrome/Edge.
- The orb requires WebGL; audio playback requires a user gesture (the mic click
  unlocks the AudioContext).
- Tool calls run synchronously within the response cycle (no background tasks).
