# JARVIS iPhone Companion (Siri Shortcut)

Talk to JARVIS from your iPhone over your local Wi-Fi. The backend exposes a
rate-limited `POST /voice` endpoint that runs your text through the same brain
pipeline and returns a spoken reply.

## Prerequisites

- Your Mac and iPhone are on the **same Wi-Fi network**.
- The JARVIS backend is running and bound to all interfaces:
  ```
  cd ~/Desktop/Jarvis/backend && source venv/bin/activate
  uvicorn main:app --host 0.0.0.0 --port 8000
  ```
  (The `jarvis` launcher already starts it this way.)
- Find your Mac's local IP. On startup the backend prints:
  ```
  📱 iPhone companion URL: http://<YOUR_MAC_IP>:8000/voice
  ```
  or check **System Settings → Wi-Fi → Details → IP Address** (e.g. `192.168.1.42`).

## Build the Shortcut

1. Open the **Shortcuts** app on your iPhone and tap **+** to create a new one.
2. Name it **Hey JARVIS** (so "Hey Siri, Hey JARVIS" works).
3. Add action **Dictate Text** (speech → text).
4. Add action **Get Contents of URL** and configure:
   - **URL:** `http://<YOUR_MAC_IP>:8000/voice`
   - **Method:** `POST`
   - **Headers:** `Content-Type` = `application/json`
   - **Request Body:** `JSON` with one field:
     - key `text` → value: the **Dictated Text** variable
5. Add action **Get Dictionary Value** → key `response` → from the URL result.
6. Add action **Speak Text** → the **Dictionary Value** from the previous step.
7. (Optional) Tap the shortcut's share icon → **Add to Home Screen** and pick a
   JARVIS icon.

## Use it

Say **"Hey Siri, Hey JARVIS"**, then speak your request. The Shortcut dictates
it, POSTs to the Mac, and speaks JARVIS's reply.

> The endpoint also returns base64 MP3 audio in the `audio` field if you'd
> rather play JARVIS's own ElevenLabs voice (use **Base64 Encode → Decode** then
> **Play Sound**) instead of the Speak Text action.

## Notes

- `/voice` is **rate-limited to 10 requests per minute**.
- CORS allows `localhost` and local-network origins (`192.168.x.x`, `10.x.x.x`).
  (Shortcuts isn't a browser, so CORS doesn't gate it — this is for web clients.)
- If requests fail, confirm the backend is on `--host 0.0.0.0` and that macOS
  firewall isn't blocking port 8000.
