/**
 * DOM/UI layer for JARVIS — Iron Man HUD edition.
 *
 * Builds the mic button, the bottom-center HUD status bar, the log/fullscreen
 * controls, and the conversation log (toggle with "L"). It also drives the live
 * readouts in the two side panels declared in index.html (clock, status,
 * uptime, session count, detected language, TTS state, wake-word state), the
 * body-level activity class that themes the HUD overlays, and the waveform
 * visualizer's state.
 */

import type { VoiceStatus } from "./voice";
import type { Waveform, WaveState } from "./waveform";

type LogRole = "user" | "jarvis";
type Activity = "standby" | "listening" | "thinking" | "speaking" | "offline";

const ACTIVITY_LABELS: Record<Activity, string> = {
  standby: "Standby",
  listening: "Listening",
  thinking: "Processing",
  speaking: "Responding",
  offline: "Offline",
};

// Map the HUD activity to the waveform's visual state.
const WAVE_STATE: Record<Activity, WaveState> = {
  standby: "idle",
  listening: "listening",
  thinking: "thinking",
  speaking: "speaking",
  offline: "idle",
};

export class UI {
  private statusBar!: HTMLDivElement;
  private stateLabel!: HTMLSpanElement;
  private transcriptEl!: HTMLDivElement;
  private micBtn!: HTMLButtonElement;
  private logPanel!: HTMLDivElement;

  // Side-panel readout elements (declared in index.html).
  private plTime: HTMLElement | null = null;
  private plStatus: HTMLElement | null = null;
  private plUptime: HTMLElement | null = null;
  private plSession: HTMLElement | null = null;
  private plVoice: HTMLElement | null = null;
  private plModel: HTMLElement | null = null;
  private prWake: HTMLElement | null = null;
  private prLang: HTMLElement | null = null;
  private prTts: HTMLElement | null = null;

  private logEntries: { role: LogRole; text: string }[] = [];
  private activity: Activity = "offline";
  private connected = false;
  private sessionCount = 0;
  private startTime = Date.now();
  private prevVoiceStatus: VoiceStatus | null = null;
  private waveform: Waveform | null = null;

  // Callbacks wired by main.ts.
  private micHandler: () => void = () => {};
  private wakeToggleHandler: () => void = () => {};

  constructor() {
    this.queryPanels();
    this.buildStatusBar();
    this.buildLogButton();
    this.buildFullscreenButton();
    this.buildTranscript();
    this.buildMicButton();
    this.buildLogPanel();
    this.bindKeys();
    this.startUptime();
    this.startClock();
    this.setActivity("offline");
  }

  /** Give the UI the waveform so it can drive its state from activity changes. */
  attachWaveform(waveform: Waveform): void {
    this.waveform = waveform;
    this.waveform.setState(WAVE_STATE[this.activity]);
  }

  // ---- Builders ----
  private queryPanels(): void {
    this.plTime = document.getElementById("pl-time");
    this.plStatus = document.getElementById("pl-status");
    this.plUptime = document.getElementById("pl-uptime");
    this.plSession = document.getElementById("pl-session");
    this.plVoice = document.getElementById("pl-voice");
    this.plModel = document.getElementById("pl-model");
    this.prWake = document.getElementById("pr-wake");
    this.prLang = document.getElementById("pr-lang");
    this.prTts = document.getElementById("pr-tts");
  }

  private buildStatusBar(): void {
    this.statusBar = document.createElement("div");
    this.statusBar.id = "status-bar";
    this.statusBar.innerHTML = `
      <span class="sb-brand">◈ JARVIS</span>
      <span class="sb-divider"></span>
      <span class="sb-state"><span class="sb-dot"></span><span class="sb-label">Offline</span></span>
      <span class="sb-divider"></span>
      <span class="sb-wake" role="button" title="Toggle wake word">Hey JARVIS 🎙</span>`;
    document.body.appendChild(this.statusBar);
    this.stateLabel = this.statusBar.querySelector(
      ".sb-label"
    ) as HTMLSpanElement;
    this.statusBar
      .querySelector(".sb-wake")
      ?.addEventListener("click", () => this.wakeToggleHandler());
  }

  private buildLogButton(): void {
    const btn = document.createElement("button");
    btn.id = "log-btn";
    btn.textContent = "Log (L)";
    btn.addEventListener("click", () => this.toggleLog());
    document.body.appendChild(btn);
  }

  private buildFullscreenButton(): void {
    const btn = document.createElement("button");
    btn.id = "fullscreen-btn";
    btn.textContent = "⛶ Fullscreen";
    btn.addEventListener("click", () => this.toggleFullscreen());
    document.body.appendChild(btn);
    // Keep the label in sync if fullscreen is toggled by other means (Esc).
    const update = (): void => {
      const fs = !!(document.fullscreenElement || (document as any).webkitFullscreenElement);
      btn.textContent = fs ? "⛶ Exit" : "⛶ Fullscreen";
    };
    document.addEventListener("fullscreenchange", update);
    document.addEventListener("webkitfullscreenchange", update as EventListener);
  }

  private toggleFullscreen(): void {
    const doc = document as any;
    const el = document.documentElement as any;
    const isFs = doc.fullscreenElement || doc.webkitFullscreenElement;
    try {
      if (!isFs) {
        const req = el.requestFullscreen || el.webkitRequestFullscreen;
        req?.call(el);
      } else {
        const exit = doc.exitFullscreen || doc.webkitExitFullscreen;
        exit?.call(doc);
      }
    } catch (err) {
      console.warn("Fullscreen toggle failed:", err);
    }
  }

  private buildTranscript(): void {
    this.transcriptEl = document.createElement("div");
    this.transcriptEl.id = "transcript";
    document.body.appendChild(this.transcriptEl);
  }

  private buildMicButton(): void {
    this.micBtn = document.createElement("button");
    this.micBtn.id = "mic-btn";
    this.micBtn.setAttribute("aria-label", "Toggle microphone");
    this.micBtn.innerHTML = `
      <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"
           stroke-linecap="round" stroke-linejoin="round">
        <path d="M12 1a3 3 0 0 0-3 3v8a3 3 0 0 0 6 0V4a3 3 0 0 0-3-3z"></path>
        <path d="M19 10v2a7 7 0 0 1-14 0v-2"></path>
        <line x1="12" y1="19" x2="12" y2="23"></line>
        <line x1="8" y1="23" x2="16" y2="23"></line>
      </svg>`;
    this.micBtn.addEventListener("click", () => this.micHandler());
    document.body.appendChild(this.micBtn);
  }

  private buildLogPanel(): void {
    this.logPanel = document.createElement("div");
    this.logPanel.id = "log-panel";
    document.body.appendChild(this.logPanel);
  }

  private bindKeys(): void {
    window.addEventListener("keydown", (e) => {
      // Ignore keypresses while typing in a field.
      const tag = (e.target as HTMLElement)?.tagName;
      if (tag === "INPUT" || tag === "TEXTAREA") return;
      if (e.key === "l" || e.key === "L") {
        this.toggleLog();
      } else if (e.key === "f" || e.key === "F") {
        this.toggleFullscreen();
      }
    });
  }

  private startUptime(): void {
    const tick = (): void => {
      const s = Math.floor((Date.now() - this.startTime) / 1000);
      const hh = String(Math.floor(s / 3600)).padStart(2, "0");
      const mm = String(Math.floor((s % 3600) / 60)).padStart(2, "0");
      const ss = String(s % 60).padStart(2, "0");
      if (this.plUptime) this.plUptime.textContent = `${hh}:${mm}:${ss}`;
    };
    tick();
    window.setInterval(tick, 1000);
  }

  private startClock(): void {
    const tick = (): void => {
      const now = new Date();
      const hh = String(now.getHours()).padStart(2, "0");
      const mm = String(now.getMinutes()).padStart(2, "0");
      const ss = String(now.getSeconds()).padStart(2, "0");
      if (this.plTime) this.plTime.textContent = `${hh}:${mm}:${ss}`;
    };
    tick();
    window.setInterval(tick, 1000);
  }

  // ---- Public API ----
  onMicToggle(handler: () => void): void {
    this.micHandler = handler;
  }

  onWakeToggle(handler: () => void): void {
    this.wakeToggleHandler = handler;
  }

  /** Drive the HUD activity state (status bar, panels, overlay theming). */
  setActivity(state: Activity): void {
    // When disconnected, everything reads offline.
    if (state !== "offline" && !this.connected) state = "offline";
    this.activity = state;

    for (const a of [
      "standby",
      "listening",
      "thinking",
      "speaking",
      "offline",
    ] as Activity[]) {
      document.body.classList.toggle(`act-${a}`, a === state);
    }

    const label = ACTIVITY_LABELS[state];
    if (this.stateLabel) this.stateLabel.textContent = label;
    if (this.plStatus) {
      this.plStatus.textContent = label;
      // Brief white→cyan flash on every state change.
      this.plStatus.classList.remove("flash");
      void this.plStatus.offsetWidth; // restart the animation
      this.plStatus.classList.add("flash");
    }
    if (this.prTts) {
      this.prTts.textContent = state === "speaking" ? "Active" : "Idle";
    }
    // MODEL row shows a transient "Processing…" while thinking.
    if (this.plModel) {
      this.plModel.textContent =
        state === "thinking" ? "Processing…" : "Haiku 4.5";
    }
    // Drive the waveform visualizer.
    this.waveform?.setState(WAVE_STATE[state]);
  }

  setMicActive(active: boolean): void {
    this.micBtn.classList.toggle("listening", active);
    if (active) {
      this.setActivity("listening");
    } else if (this.activity === "listening") {
      // Capture ended without moving on to thinking — settle to standby.
      this.setActivity("standby");
    }
  }

  /** Show that JARVIS is speaking and the mic button will interrupt it. */
  setSpeaking(speaking: boolean): void {
    this.micBtn.classList.toggle("speaking", speaking);
    this.micBtn.setAttribute(
      "aria-label",
      speaking ? "Interrupt JARVIS" : "Toggle microphone"
    );
    this.micBtn.title = speaking ? "Click to interrupt JARVIS" : "";
    if (speaking) {
      this.setActivity("speaking");
    } else if (this.activity === "speaking") {
      this.setActivity("standby");
    }
  }

  setVoiceStatus(status: VoiceStatus): void {
    const armed = status === "wake" || status === "session";
    if (this.prWake) {
      this.prWake.textContent = armed ? "Armed" : "Disabled";
      // Flash the row when the wake word fires (wake/off -> session).
      if (status === "session" && this.prevVoiceStatus !== "session") {
        this.prWake.classList.remove("flash");
        void this.prWake.offsetWidth;
        this.prWake.classList.add("flash");
      }
    }
    this.prevVoiceStatus = status;
  }

  setStatus(state: "connected" | "disconnected"): void {
    this.connected = state === "connected";
    if (!this.connected) {
      this.setActivity("offline");
    } else if (this.activity === "offline") {
      this.setActivity("standby");
    }
  }

  showTranscript(text: string, interim = false): void {
    this.transcriptEl.textContent = text;
    this.transcriptEl.classList.toggle("interim", interim);
  }

  clearTranscript(): void {
    this.transcriptEl.textContent = "";
    this.transcriptEl.classList.remove("interim");
  }

  addToLog(role: LogRole, text: string): void {
    this.logEntries.push({ role, text });
    if (this.logEntries.length > 20) {
      this.logEntries = this.logEntries.slice(-20);
    }
    this.renderLog();

    if (role === "user") {
      this.sessionCount += 1;
      if (this.plSession) {
        const noun = this.sessionCount === 1 ? "Query" : "Queries";
        this.plSession.textContent = `${this.sessionCount} ${noun}`;
      }
      if (this.prLang) this.prLang.textContent = detectLang(text);
    }
  }

  private renderLog(): void {
    this.logPanel.innerHTML = this.logEntries
      .map((entry) => {
        const roleLabel = entry.role === "user" ? "You" : "JARVIS";
        return `<div class="entry ${entry.role}"><span class="role">${roleLabel}</span>${escapeHtml(
          entry.text
        )}</div>`;
      })
      .join("");
    this.logPanel.scrollTop = this.logPanel.scrollHeight;
  }

  private toggleLog(): void {
    this.logPanel.classList.toggle("open");
  }
}

/** Lightweight client-side language tag for the HUD readout (cosmetic). */
function detectLang(text: string): "EN" | "SK" | "IT" | "CS" {
  const t = text.toLowerCase();
  if (/[řůě]/.test(t) || /\b(děkuji|díky|proč|nevím|dobře)\b/.test(t))
    return "CS";
  if (/[äľôťďĺŕ]/.test(t) || /\b(ahoj|ďakujem|prečo|prosím)\b/.test(t))
    return "SK";
  if (/[àèìòù]/.test(t) || /\b(ciao|grazie|perché|prego|quando)\b/.test(t))
    return "IT";
  return "EN";
}

function escapeHtml(s: string): string {
  return s
    .replace(/&/g, "&amp;")
    .replace(/</g, "&lt;")
    .replace(/>/g, "&gt;");
}
