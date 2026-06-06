/**
 * Speech-to-text via the Web Speech API, with always-on "Hey JARVIS" wake-word
 * detection.
 *
 * Two recognizers are used:
 *  - a background listener (continuous) that watches only for wake phrases, and
 *  - a foreground listener that captures the actual command after waking.
 *
 * They never run at the same time (they'd fight over the mic): waking stops the
 * background listener, runs the command listener, then the background listener
 * is resumed once the command + response cycle completes.
 *
 * Falls back to manual-button-only operation on unsupported browsers.
 */

const WAKE_PHRASES = [
  "hey jarvis",
  "hi jarvis",
  "okay jarvis",
  "ok jarvis",
  "jarvis",
];

export class VoiceInput {
  private recognition: any | null = null; // foreground (command) recognizer
  private wakeRecognition: any | null = null; // background (wake-word) recognizer
  private supported = false;

  private listening = false; // command recognizer active
  private wakeEnabled = false; // user preference: wake word on/off
  private wakeRunning = false; // background recognizer actually running
  private waking = false; // transient: wake detected, switching to command
  private wakeErrored = false; // last background end was preceded by an error
  private restartTimer: number | null = null;

  private beepCtx: AudioContext | null = null;

  // Callbacks (wired by main.ts).
  private finalCb: (text: string) => void = () => {};
  private interimCb: (text: string) => void = () => {};
  private stateCb: (listening: boolean) => void = () => {};
  private wakeCb: () => void = () => {};
  private wakeStatusCb: (active: boolean) => void = () => {};

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
      if (final.trim()) this.finalCb(final.trim());
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
      if (this.waking || this.listening) return;
      let transcript = "";
      for (let i = event.resultIndex; i < event.results.length; i++) {
        transcript += event.results[i][0].transcript;
      }
      const lower = transcript.toLowerCase();
      if (WAKE_PHRASES.some((p) => lower.includes(p))) {
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
        this._emitWakeStatus();
      }
    };

    // `end` always follows `error`, so do all restart scheduling here.
    r.onend = () => {
      this.wakeRunning = false;
      if (this.wakeEnabled && !this.waking && !this.listening) {
        // Auto-restart after an error (1s) keeps it always-on; a clean end
        // (the browser stopping a "continuous" recognizer) restarts quickly.
        this._scheduleWakeRestart(this.wakeErrored ? 1000 : 300);
      }
      this.wakeErrored = false;
    };

    this.wakeRecognition = r;
  }

  // ---- Wake-word detection flow ----
  private _onWakeDetected(): void {
    if (this.waking || this.listening) return;
    this.waking = true;
    this._beep();
    this.wakeCb(); // main.ts: orb -> listening, show "Listening..."
    this.start(); // stop background listener + begin command capture
  }

  /** Short 880Hz confirmation beep (~100ms) via a Web Audio oscillator. */
  private _beep(): void {
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
      osc.frequency.value = 880;
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

  private _emitWakeStatus(): void {
    this.wakeStatusCb(this.wakeEnabled && this.supported);
  }

  // ---- Public command API ----
  isSupported(): boolean {
    return this.supported;
  }

  /** Start capturing a command (used by the mic button and by wake detection). */
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
        this.resumeWakeWord();
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

  // ---- Public wake-word API ----
  isWakeWordSupported(): boolean {
    return this.supported;
  }

  isWakeWordEnabled(): boolean {
    return this.wakeEnabled;
  }

  /** Enable + start the background wake listener. */
  startWakeWord(): void {
    if (!this.supported) return;
    this.wakeEnabled = true;
    if (!this.listening) this._startWakeRec();
    this._emitWakeStatus();
  }

  /** Disable + stop the background wake listener. */
  stopWakeWord(): void {
    this.wakeEnabled = false;
    this._stopWakeRec();
    this._emitWakeStatus();
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
   * repeatedly — no-ops while a command is being captured or wake word is off.
   */
  resumeWakeWord(): void {
    if (!this.supported || !this.wakeEnabled || this.listening) return;
    this._startWakeRec();
    this._emitWakeStatus();
  }

  onWake(cb: () => void): void {
    this.wakeCb = cb;
  }

  onWakeStatusChange(cb: (active: boolean) => void): void {
    this.wakeStatusCb = cb;
  }
}
