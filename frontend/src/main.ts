import { OrbVisualizer, type Mood } from "./orb";
import { VoiceInput } from "./voice";
import { WSClient } from "./websocket";
import { UI } from "./ui";

/** Infer a mood from JARVIS's response text to theme the orb's colour. */
function detectMood(text: string): Mood {
  const t = text.toLowerCase();
  // Briefings mention weather + email keywords, so check them first.
  if (/(good morning|briefing|summary)/.test(t)) return "morning_briefing";
  if (/(playing|song|track|music|spotify)/.test(t)) return "music";
  if (/(°|weather|temperature|rain|sunny|cloudy|forecast)/.test(t))
    return "weather";
  if (/(calendar|meeting|email|reminder|inbox|unread)/.test(t)) return "email";
  if (/(error|sorry|can't|cannot|can not|unable|couldn't|could not)/.test(t))
    return "error";
  if (
    /(great|happy|awesome|wonderful|glad|congrats?|excellent|good news|nice)/.test(
      t
    )
  )
    return "happy";
  return "neutral";
}

/**
 * App entry point: instantiates the orb, voice input ("Hey JARVIS" wake word +
 * active-session mode), WebSocket client, and UI, then wires them together.
 */
function main(): void {
  const canvas = document.getElementById("orb-canvas") as HTMLCanvasElement;

  const orb = new OrbVisualizer(canvas);
  const voice = new VoiceInput();
  const ws = new WSClient("ws://localhost:8000/ws");
  const ui = new UI();

  ws.attachOrb(orb);

  // True while we're waiting on a backend response, so we don't start the next
  // listen (or re-arm wake word) until the command/response cycle completes.
  let pendingResponse = false;

  // Resume listening appropriately once a turn finishes: keep going if we're in
  // an active session, otherwise idle the orb and re-arm the wake word.
  const afterTurn = () => {
    if (voice.isSessionActive()) {
      voice.continueListening();
    } else {
      orb.setState("idle");
      voice.resumeWakeWord();
    }
  };

  // ---- Connection status ----
  ws.onStatus((connected) => {
    ui.setStatus(connected ? "connected" : "disconnected");
  });

  // ---- Mic button (manual): start/stop an active session ----
  ui.onMicToggle(() => {
    orb.resume(); // unlock AudioContext on user gesture
    if (voice.isSessionActive() || voice.isListening()) {
      voice.endSession();
    } else {
      if (!voice.isSupported()) {
        ui.showTranscript("Speech recognition not supported in this browser.");
        return;
      }
      voice.enterSession();
    }
  });

  // ---- Indicator click: end session, or toggle wake word ----
  ui.onWakeToggle(() => {
    orb.resume();
    if (voice.isSessionActive()) voice.endSession();
    else voice.toggleWakeWord();
  });

  // ---- Voice status drives the indicator (session / wake / off / unsupported) ----
  voice.onStatusChange((status) => ui.setVoiceStatus(status));

  // ---- Command listening state ----
  voice.onStateChange((listening) => {
    ui.setMicActive(listening);
    if (listening) {
      orb.setState("listening");
      orb.setMood("neutral"); // reset colour theme for a new command
      ui.showTranscript("Listening...");
    } else if (!pendingResponse) {
      // Capture ended with no command (or was stopped) — continue or sleep.
      afterTurn();
    }
  });

  voice.onInterim((text) => {
    ui.showTranscript(text, true);
  });

  voice.onResult((text) => {
    ui.showTranscript(text, false);
    ui.addToLog("user", text);
    if (ws.isConnected()) {
      pendingResponse = true;
      ws.send(text); // flips orb into "thinking"
    } else {
      ui.showTranscript("Not connected to JARVIS backend.");
      afterTurn();
    }
  });

  // ---- Going to sleep (stop phrase / silence timeout / manual) ----
  voice.onSleep(() => {
    orb.setState("idle");
    ui.showTranscript("Going to sleep 💤", false);
  });

  // ---- Responses from the backend ----
  ws.onResponse(({ text }) => {
    pendingResponse = false;
    ui.showTranscript(text, false);
    ui.addToLog("jarvis", text);
    orb.setMood(detectMood(text)); // theme the orb to the response's mood
    // Orb state is handled by the WS client (speaking → idle). Then continue
    // the session (auto-listen) or re-arm the wake word.
    afterTurn();
  });

  // ---- Start the always-on wake listener on load ----
  if (voice.isWakeWordSupported()) {
    voice.startWakeWord();
  }
  ui.setVoiceStatus(voice.currentStatus());
}

window.addEventListener("DOMContentLoaded", main);
