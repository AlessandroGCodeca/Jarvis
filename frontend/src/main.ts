import { OrbVisualizer } from "./orb";
import { VoiceInput } from "./voice";
import { WSClient } from "./websocket";
import { UI } from "./ui";

/**
 * App entry point: instantiates the orb, voice input (with "Hey JARVIS" wake
 * word), WebSocket client, and UI, then wires the events that connect them.
 */
function main(): void {
  const canvas = document.getElementById("orb-canvas") as HTMLCanvasElement;

  const orb = new OrbVisualizer(canvas);
  const voice = new VoiceInput();
  const ws = new WSClient("ws://localhost:8000/ws");
  const ui = new UI();

  ws.attachOrb(orb);

  // True while we're waiting on a backend response — so we don't re-arm the
  // wake listener until the command/response cycle is fully complete.
  let pendingResponse = false;

  // ---- Connection status ----
  ws.onStatus((connected) => {
    ui.setStatus(connected ? "connected" : "disconnected");
  });

  // ---- Mic button (manual) toggles command listening ----
  ui.onMicToggle(() => {
    orb.resume(); // unlock AudioContext on user gesture
    if (voice.isListening()) {
      voice.stop();
    } else {
      if (!voice.isSupported()) {
        ui.showTranscript("Speech recognition not supported in this browser.");
        return;
      }
      voice.start(); // stops the wake listener + begins command capture
    }
  });

  // ---- Wake-word indicator toggles wake word on/off ----
  ui.onWakeToggle(() => {
    orb.resume();
    voice.toggleWakeWord();
  });

  voice.onWakeStatusChange((active) => {
    ui.setWakeStatus(active, voice.isWakeWordSupported());
  });

  // ---- Wake word detected: immediate feedback (beep is played in voice) ----
  voice.onWake(() => {
    orb.resume();
    orb.setState("listening");
    ui.showTranscript("Listening...");
  });

  // ---- Command listening state ----
  voice.onStateChange((listening) => {
    ui.setMicActive(listening);
    if (listening) {
      orb.setState("listening");
      ui.showTranscript("Listening...");
    } else if (!pendingResponse) {
      // No command was sent (empty capture or manual stop) — settle and re-arm.
      orb.setState("idle");
      voice.resumeWakeWord();
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
      orb.setState("idle");
      voice.resumeWakeWord();
    }
  });

  // ---- Responses from the backend ----
  ws.onResponse(({ text }) => {
    pendingResponse = false;
    ui.showTranscript(text, false);
    ui.addToLog("jarvis", text);
    // Orb state is handled by the WS client (speaking → idle). Re-arm wake word.
    voice.resumeWakeWord();
  });

  // ---- Start the always-on wake listener on load ----
  if (voice.isWakeWordSupported()) {
    voice.startWakeWord();
  }
  ui.setWakeStatus(voice.isWakeWordEnabled(), voice.isWakeWordSupported());
}

window.addEventListener("DOMContentLoaded", main);
