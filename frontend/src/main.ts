import { OrbVisualizer } from "./orb";
import { VoiceInput } from "./voice";
import { WSClient } from "./websocket";
import { UI } from "./ui";

/**
 * App entry point: instantiates the orb, voice input, WebSocket client, and UI,
 * then wires the events that connect them.
 */
function main(): void {
  const canvas = document.getElementById("orb-canvas") as HTMLCanvasElement;

  const orb = new OrbVisualizer(canvas);
  const voice = new VoiceInput();
  const ws = new WSClient("ws://localhost:8000/ws");
  const ui = new UI();

  ws.attachOrb(orb);

  // ---- Connection status ----
  ws.onStatus((connected) => {
    ui.setStatus(connected ? "connected" : "disconnected");
  });

  // ---- Mic button toggles listening ----
  ui.onMicToggle(() => {
    orb.resume(); // unlock AudioContext on user gesture
    if (voice.isListening()) {
      voice.stop();
    } else {
      if (!voice.isSupported()) {
        ui.showTranscript("Speech recognition not supported in this browser.");
        return;
      }
      voice.start();
    }
  });

  // ---- Voice: state, interim, final ----
  voice.onStateChange((listening) => {
    ui.setMicActive(listening);
    if (listening) {
      orb.setState("listening");
      ui.clearTranscript();
    } else {
      // If we aren't mid-thinking/speaking, settle back to idle.
      orb.setState("idle");
    }
  });

  voice.onInterim((text) => {
    ui.showTranscript(text, true);
  });

  voice.onResult((text) => {
    ui.showTranscript(text, false);
    ui.addToLog("user", text);
    ws.send(text); // flips orb into "thinking"
  });

  // ---- Responses from the backend ----
  ws.onResponse(({ text }) => {
    ui.showTranscript(text, false);
    ui.addToLog("jarvis", text);
  });
}

window.addEventListener("DOMContentLoaded", main);
