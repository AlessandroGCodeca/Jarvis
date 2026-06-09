/**
 * Speech-to-text via the Web Speech API, with "Hey JARVIS" wake-word detection
 * and a follow-on "active session" mode.
 *
 * Modes:
 *  - WAKE: a background recognizer (continuous) listens only for wake phrases.
 *  - SESSION: after waking, JARVIS keeps accepting commands automatically —
 *    each response is followed by another listen, no wake word needed. The
 *    session ends on a stop phrase ("goodbye", "go to sleep", ...), after 60s
 *    of silence, or manually; control then returns to WAKE.
 *
 * The wake recognizer and the command recognizer never run together (they'd
 * fight over the mic). Falls back to manual-button-only on unsupported browsers.
 */

// Bare "jarvis" is intentionally excluded — it caused false wakes from any
// sentence containing the word. The phrase must also appear at the START of
// the transcript (see the wake recognizer below).
const WAKE_PHRASES = ["hey jarvis", "hi jarvis", "okay jarvis", "ok jarvis"];

const STOP_PHRASES = [
  "goodbye",
  "good bye",
  "bye jarvis",
  "go to sleep",
  "stop listening",
  "that's all",
  "thats all",
];

const SESSION_SILENCE_MS = 60_000; // auto-sleep after 1 min with no commands

export type VoiceStatus = "session" | "wake" | "off" | "unsupported";

export class VoiceInput {
  private recognition: any | null = null; // foreground (command) recognizer
  private wakeRecognition: any | null = null; // background (wake-word) recognizer
  private supported = false;

  private listening = false; // command recognizer active
  private sessionActive = false; // active-session mode (auto-listen between turns)
  private wakeEnabled = false; // user preference: wake word on/off
  private wakeRunning = false; // background recognizer actually running
  private waking = false; // transient: wake detected, switching to command
  private wakeErrored = false; // last background end was preceded by an error
  private restartTimer: number | null = null;
  private silenceTimer: number | null = null;

  private beepCtx: AudioContext | null = null;

  // Lets the voice layer interrupt JARVIS's speech when the user barges in.
  private playbackController: {
    isPlaying: () => boolean;
    stop: () => void;
  } | null = null;

  // Callbacks (wired by main.ts).
  private finalCb: (text: string) => void = () => {};
  private interimCb: (text: string) => void = () => {};
  private stateCb: (listening: boolean) => void = () => {};
  private statusCb: (status: VoiceStatus) => void = () => {};
  private sleepCb: () => void = () => {};

  constructor() {
    const SR =
      (window as any).SpeechRecognition ||
      (window as any).webkitSpeechRecognition;

    if (!SR) {
      console.warn("Web Speech API not supported — wake word disabled.");
      return;
    }

    this.supported = true;
    this._initCommandRecognizer(SR);
    this._initWakeRecognizer(SR);

    // Re-arm the background listener when the tab regains focus/visibility.
    window.addEventListener("focus", () => this.resumeWakeWord());
    document.addEventListener("visibilitychange", () => {
      if (!document.hidden) this.resumeWakeWord();
    });
  }

  // ---- Recognizer setup ----
  private _initCommandRecognizer(SR: any): void {
    const recognition = new SR();
    recognition.continuous = false;
    recognition.interimResults = true;
    recognition.lang = "en-US";

    recognition.onresult = (event: any) => {
      let interim = "";
      let final = "";
      for (let i = event.resultIndex; i < event.results.length; i++) {
        const transcript = event.results[i][0].transcript;
        if (event.results[i].isFinal) final += transcript;
        else interim += transcript;
      }
      if (interim) this.interimCb(interim);
      if (final.trim()) {
        const text = final.trim();
        // In a session, a stop phrase puts JARVIS back to sleep (not sent on).
        if (this.sessionActive && this._isStopPhrase(text)) {
          this.endSession();
          return;
        }
        if (this.sessionActive) this._startSilenceTimer(); // reset on command
        this.finalCb(text);
      }
    };

    recognition.onend = () => {
      this.listening = false;
      this.stateCb(false);
    };

    recognition.onerror = (event: any) => {
      console.warn("Command recognition error:", event.error);
      this.listening = false;
      this.stateCb(false);
    };

    this.recognition = recognition;
  }

  private _initWakeRecognizer(SR: any): void {
    const r = new SR();
    r.continuous = true;
    r.interimResults = true;
    r.lang = "en-US";

    r.onresult = (event: any) => {
      if (this.waking || this.listening || this.sessionActive) return;
      let transcript = "";
      for (let i = event.resultIndex; i < event.results.length; i++) {
        transcript += event.results[i][0].transcript;
      }
      // Require the wake phrase at the very start of the utterance to avoid
      // false triggers from "jarvis" appearing mid-sentence.
      const lower = transcript.trim().toLowerCase();
      if (WAKE_PHRASES.some((p) => lower.startsWith(p))) {
        this._onWakeDetected();
      }
    };

    r.onerror = (event: any) => {
      this.wakeErrored = true;
      // A permission denial would otherwise retry forever — stop and fall back.
      if (
        event.error === "not-allowed" ||
        event.error === "service-not-allowed"
      ) {
        this.wakeEnabled = false;
        this.wakeRunning = false;
        this._emitStatus();
      }
    };

    // `end` always follows `error`, so do all restart scheduling here.
    r.onend = () => {
      this.wakeRunning = false;
      if (
        this.wakeEnabled &&
        !this.waking &&
        !this.listening &&
        !this.sessionActive
      ) {
        this._scheduleWakeRestart(this.wakeErrored ? 1000 : 300);
      }
      this.wakeErrored = false;
    };

    this.wakeRecognition = r;
  }

  /** Wire a controller so a wake-word can interrupt JARVIS mid-speech. */
  setPlaybackController(controller: {
    isPlaying: () => boolean;
    stop: () => void;
  }): void {
    this.playbackController = controller;
  }

  // ---- Wake-word detection -> session ----
  private _onWakeDetected(): void {
    if (this.waking || this.listening || this.sessionActive) return;
    // Barge-in: if JARVIS is speaking, cut it off before we start listening.
    if (this.playbackController?.isPlaying()) {
      this.playbackController.stop();
    }
    this.waking = true;
    this.enterSession();
  }

  private _isStopPhrase(text: string): boolean {
    const lower = text.toLowerCase();
    return STOP_PHRASES.some((p) => lower.includes(p));
  }

  /** Short confirmation tone (~100ms) via a Web Audio oscillator. */
  private _beep(freq = 880): void {
    try {
      if (!this.beepCtx) {
        const Ctx =
          (window as any).AudioContext || (window as any).webkitAudioContext;
        this.beepCtx = new Ctx();
      }
      const ctx = this.beepCtx!;
      if (ctx.state === "suspended") void ctx.resume();
      const osc = ctx.createOscillator();
      const gain = ctx.createGain();
      osc.type = "sine";
      osc.frequency.value = freq;
      const now = ctx.currentTime;
      gain.gain.setValueAtTime(0.0001, now);
      gain.gain.exponentialRampToValueAtTime(0.2, now + 0.01);
      gain.gain.exponentialRampToValueAtTime(0.0001, now + 0.1);
      osc.connect(gain);
      gain.connect(ctx.destination);
      osc.start(now);
      osc.stop(now + 0.1);
    } catch (err) {
      console.warn("beep failed", err);
    }
  }

  // ---- Background recognizer control ----
  private _startWakeRec(): void {
    if (!this.wakeRecognition || this.wakeRunning || this.listening) return;
    try {
      this.wakeRecognition.start();
      this.wakeRunning = true;
    } catch {
      // Usually InvalidStateError because it's already running — treat as on.
      this.wakeRunning = true;
    }
  }

  private _stopWakeRec(): void {
    if (this.restartTimer !== null) {
      clearTimeout(this.restartTimer);
      this.restartTimer = null;
    }
    if (!this.wakeRecognition) return;
    try {
      this.wakeRecognition.abort();
    } catch {
      /* ignore */
    }
    this.wakeRunning = false;
  }

  private _scheduleWakeRestart(ms: number): void {
    if (this.restartTimer !== null) clearTimeout(this.restartTimer);
    this.restartTimer = window.setTimeout(() => {
      this.restartTimer = null;
      this.resumeWakeWord();
    }, ms);
  }

  // ---- Silence (auto-sleep) timer ----
  private _startSilenceTimer(): void {
    this._clearSilenceTimer();
    this.silenceTimer = window.setTimeout(() => {
      this.silenceTimer = null;
      if (this.sessionActive) this.endSession();
    }, SESSION_SILENCE_MS);
  }

  private _clearSilenceTimer(): void {
    if (this.silenceTimer !== null) {
      clearTimeout(this.silenceTimer);
      this.silenceTimer = null;
    }
  }

  private _emitStatus(): void {
    this.statusCb(this.currentStatus());
  }

  // ---- Public command API ----
  isSupported(): boolean {
    return this.supported;
  }

  /** Start capturing a single command (used internally and by `enterSession`). */
  start(): void {
    if (!this.recognition || this.listening) return;
    const wasWakeRunning = this.wakeRunning;
    this._stopWakeRec(); // free the mic from the background listener

    const begin = () => {
      if (this.listening) return;
      try {
        this.recognition.start();
        this.listening = true;
        this.waking = false;
        this.stateCb(true);
      } catch (err) {
        console.warn("Could not start recognition:", err);
        this.waking = false;
        if (this.sessionActive) this.endSession();
        else this.resumeWakeWord();
      }
    };

    // Give the mic a beat to release before the command recognizer grabs it.
    if (wasWakeRunning) window.setTimeout(begin, 150);
    else begin();
  }

  stop(): void {
    if (!this.recognition || !this.listening) return;
    this.recognition.stop();
    this.listening = false;
    this.stateCb(false);
  }

  isListening(): boolean {
    return this.listening;
  }

  onResult(cb: (text: string) => void): void {
    this.finalCb = cb;
  }

  onInterim(cb: (text: string) => void): void {
    this.interimCb = cb;
  }

  onStateChange(cb: (listening: boolean) => void): void {
    this.stateCb = cb;
  }

  // ---- Active-session API ----
  isSessionActive(): boolean {
    return this.sessionActive;
  }

  /** Enter active-session mode and begin listening for a command. */
  enterSession(): void {
    if (!this.supported || this.sessionActive) return;
    this.sessionActive = true;
    this._beep(880); // wake/confirm tone
    this._emitStatus(); // -> "session" (cyan)
    this.start(); // stop wake listener + begin command capture
    this._startSilenceTimer();
  }

  /** Continue an active session: listen for the next command (small gap). */
  continueListening(): void {
    if (!this.sessionActive || this.listening) return;
    window.setTimeout(() => {
      if (this.sessionActive && !this.listening) this.start();
    }, 300);
  }

  /** End the active session and return to wake-word sleep mode. */
  endSession(): void {
    const wasActive = this.sessionActive;
    this.sessionActive = false;
    this._clearSilenceTimer();
    if (this.recognition && this.listening) {
      try {
        this.recognition.abort(); // onend will clear `listening` + emit state
      } catch {
        /* ignore */
      }
    }
    if (wasActive) {
      this._beep(440); // lower "going to sleep" tone
      this.sleepCb();
    }
    this._emitStatus(); // -> "wake" or "off"
    this.resumeWakeWord();
  }

  onSleep(cb: () => void): void {
    this.sleepCb = cb;
  }

  // ---- Public wake-word API ----
  isWakeWordSupported(): boolean {
    return this.supported;
  }

  isWakeWordEnabled(): boolean {
    return this.wakeEnabled;
  }

  currentStatus(): VoiceStatus {
    if (!this.supported) return "unsupported";
    if (this.sessionActive) return "session";
    if (this.wakeEnabled) return "wake";
    return "off";
  }

  /** Enable + start the background wake listener. */
  startWakeWord(): void {
    if (!this.supported) return;
    this.wakeEnabled = true;
    if (!this.listening && !this.sessionActive) this._startWakeRec();
    this._emitStatus();
  }

  /** Disable + stop the background wake listener. */
  stopWakeWord(): void {
    this.wakeEnabled = false;
    this._stopWakeRec();
    this._emitStatus();
  }

  /** Toggle wake word on/off; returns the new enabled state. */
  toggleWakeWord(): boolean {
    if (!this.supported) return false;
    if (this.wakeEnabled) this.stopWakeWord();
    else this.startWakeWord();
    return this.wakeEnabled;
  }

  /**
   * Re-arm the background listener if it should be running. Safe to call
   * repeatedly — no-ops during a command, an active session, or when off.
   */
  resumeWakeWord(): void {
    if (
      !this.supported ||
      !this.wakeEnabled ||
      this.listening ||
      this.sessionActive
    ) {
      return;
    }
    this._startWakeRec();
    this._emitStatus();
  }

  onStatusChange(cb: (status: VoiceStatus) => void): void {
    this.statusCb = cb;
  }
}
