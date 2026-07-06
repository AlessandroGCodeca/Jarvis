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
 *
 * Engines:
 *  - "speech": the Web Speech API path above (Chrome/Edge — reliable there).
 *  - "recorder": click-to-talk via MediaRecorder + the backend /stt endpoint
 *    (ElevenLabs Scribe). Used in Safari — its SpeechRecognition exists but is
 *    flaky, especially in continuous mode — on browsers with no
 *    SpeechRecognition at all, and as a live fallback when the speech service
 *    errors with 'service-not-allowed' or repeated 'network' failures.
 *    Recording stops on the second mic click or after a trailing silence,
 *    then the transcript feeds the same onResult path as the speech engine.
 *    No wake word in this mode (it needs a continuous recognizer).
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

// ---- Recorder-engine tuning (Safari / no-SpeechRecognition fallback) ----
const STT_URL = "http://localhost:8000/stt";
const RECORD_MAX_MS = 15_000; // hard cap on one recorded command
const SILENCE_STOP_MS = 1_600; // stop this long after the speaker goes quiet
const NO_SPEECH_MS = 8_000; // give up if nothing was said at all
const VOICE_RMS_THRESHOLD = 0.04; // time-domain RMS above this counts as speech

/** True for real Safari (Chrome/Edge/Opera also put "Safari" in their UA). */
function isSafari(): boolean {
  const ua = navigator.userAgent;
  return /Safari\//.test(ua) && !/Chrom|CriOS|FxiOS|Edg|OPR|Android/.test(ua);
}

type Engine = "speech" | "recorder" | "none";

export type VoiceStatus = "session" | "wake" | "off" | "unsupported";

export class VoiceInput {
  private recognition: any | null = null; // foreground (command) recognizer
  private wakeRecognition: any | null = null; // background (wake-word) recognizer
  private supported = false;
  private engine: Engine = "none";

  // Recorder-engine state (see the module docstring).
  private recorder: MediaRecorder | null = null;
  private recorderChunks: Blob[] = [];
  private recording = false;
  private sendOnStop = true;
  private silencePoll: number | null = null;
  private silenceSource: MediaStreamAudioSourceNode | null = null;
  private networkErrors = 0; // consecutive 'network' errors before demoting

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
  private errorCb: (message: string) => void = () => {};

  constructor() {
    const SR =
      (window as any).SpeechRecognition ||
      (window as any).webkitSpeechRecognition;

    // Safari exposes webkitSpeechRecognition but it's unreliable (especially
    // continuous mode), so it goes straight to the recorder engine.
    if (SR && !isSafari()) {
      this.engine = "speech";
      this.supported = true;
      this._initCommandRecognizer(SR);
      this._initWakeRecognizer(SR);

      // Re-arm the background listener when the tab regains focus/visibility.
      window.addEventListener("focus", () => this.resumeWakeWord());
      document.addEventListener("visibilitychange", () => {
        if (!document.hidden) this.resumeWakeWord();
      });
    } else if (this._recorderAvailable()) {
      this.engine = "recorder";
      this.supported = true;
      console.info(
        "Using MediaRecorder + server-side STT for voice input " +
          "(SpeechRecognition missing or unreliable in this browser)."
      );
    } else {
      console.warn("No SpeechRecognition or MediaRecorder — voice disabled.");
    }
  }

  private _recorderAvailable(): boolean {
    return (
      typeof navigator.mediaDevices?.getUserMedia === "function" &&
      typeof (window as any).MediaRecorder === "function"
    );
  }

  // ---- Recognizer setup ----
  private _initCommandRecognizer(SR: any): void {
    const recognition = new SR();
    recognition.continuous = false;
    recognition.interimResults = true;
    recognition.lang = "en-US";

    recognition.onresult = (event: any) => {
      this.networkErrors = 0; // the service is clearly reachable again
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
      this._handleRecognitionError(event.error, "command");
    };

    this.recognition = recognition;
  }

  /**
   * Surface a Web Speech API error to the HUD and, when the recognition
   * service itself is unusable ('service-not-allowed', or repeated 'network'
   * failures), demote this page to the recorder engine so voice keeps working.
   */
  private _handleRecognitionError(
    error: string,
    source: "command" | "wake"
  ): void {
    if (error === "aborted") return; // we abort recognizers ourselves routinely
    if (error === "no-speech") {
      // Routine timeout, not a fault — a quiet hint instead of an error.
      if (source === "command") this.interimCb("No speech detected.");
      return;
    }
    if (error === "not-allowed") {
      this.errorCb(
        "Microphone access denied — allow the mic for this site in your " +
          "browser settings."
      );
      return;
    }

    if (error === "network") this.networkErrors += 1;
    else this.networkErrors = 0;
    const serviceBroken =
      error === "service-not-allowed" ||
      (error === "network" && this.networkErrors >= 2);

    if (serviceBroken && this._recorderAvailable()) {
      this._demoteToRecorder(error);
      return;
    }
    this.errorCb(`Speech recognition error: ${error}`);
  }

  /** Switch this page from the speech engine to the recorder engine. */
  private _demoteToRecorder(reason: string): void {
    if (this.engine === "recorder") return;
    this.engine = "recorder";
    this.sessionActive = false;
    this._clearSilenceTimer();
    this.wakeEnabled = false;
    this._stopWakeRec();
    try {
      this.recognition?.abort();
    } catch {
      /* ignore */
    }
    this.listening = false;
    this.stateCb(false);
    this._emitStatus();
    this.errorCb(
      `Speech service unavailable (${reason}) — switched to recorded voice ` +
        "input. Click the mic, speak, then click again (or pause) to send."
    );
  }

  private _initWakeRecognizer(SR: any): void {
    const r = new SR();
    r.continuous = true;
    r.interimResults = true;
    r.lang = "en-US";

    r.onresult = (event: any) => {
      this.networkErrors = 0; // the service is clearly reachable again
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
      this._handleRecognitionError(event.error, "wake");
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

  /** Lazily get the shared AudioContext (resumed), or null if unavailable. */
  private _ensureCtx(): AudioContext | null {
    try {
      if (!this.beepCtx) {
        const Ctx =
          (window as any).AudioContext || (window as any).webkitAudioContext;
        this.beepCtx = new Ctx();
      }
      if (this.beepCtx!.state === "suspended") void this.beepCtx!.resume();
      return this.beepCtx;
    } catch {
      return null;
    }
  }

  /** Schedule one oscillator tone with an exponential fade-out. */
  private _tone(
    ctx: AudioContext,
    freq: number,
    startOffset: number,
    duration: number,
    type: OscillatorType,
    volume: number
  ): void {
    const osc = ctx.createOscillator();
    const gain = ctx.createGain();
    osc.type = type;
    osc.frequency.value = freq;
    const t0 = ctx.currentTime + startOffset;
    gain.gain.setValueAtTime(volume, t0);
    gain.gain.exponentialRampToValueAtTime(0.001, t0 + duration);
    osc.connect(gain);
    gain.connect(ctx.destination);
    osc.start(t0);
    osc.stop(t0 + duration + 0.05);
  }

  /** A brief white-noise "snap" for the system-activating feel. */
  private _noiseBurst(ctx: AudioContext, durationMs: number, volume: number): void {
    const len = Math.max(1, Math.floor((ctx.sampleRate * durationMs) / 1000));
    const buffer = ctx.createBuffer(1, len, ctx.sampleRate);
    const data = buffer.getChannelData(0);
    for (let i = 0; i < len; i++) data[i] = Math.random() * 2 - 1;
    const src = ctx.createBufferSource();
    src.buffer = buffer;
    const gain = ctx.createGain();
    gain.gain.value = volume;
    src.connect(gain);
    gain.connect(ctx.destination);
    src.start();
  }

  /** Iron Man-style ascending wake chime (~620ms) + a noise snap. */
  playWakeSound(): void {
    const ctx = this._ensureCtx();
    if (!ctx) return;
    try {
      this._noiseBurst(ctx, 60, 0.05);
      this._tone(ctx, 220, 0.0, 0.15, "sine", 0.15);
      this._tone(ctx, 440, 0.08, 0.2, "sine", 0.2);
      this._tone(ctx, 880, 0.18, 0.18, "sine", 0.25);
      this._tone(ctx, 1320, 0.28, 0.12, "triangle", 0.15);
      this._tone(ctx, 660, 0.32, 0.3, "sine", 0.1);
    } catch (err) {
      console.warn("wake sound failed", err);
    }
  }

  /** Descending power-down chime (~400ms) for sleep / session end. */
  playShutdownSound(): void {
    const ctx = this._ensureCtx();
    if (!ctx) return;
    try {
      this._tone(ctx, 1320, 0.0, 0.1, "sine", 0.18);
      this._tone(ctx, 880, 0.1, 0.1, "sine", 0.15);
      this._tone(ctx, 440, 0.2, 0.1, "sine", 0.12);
      this._tone(ctx, 220, 0.3, 0.12, "sine", 0.1);
    } catch (err) {
      console.warn("shutdown sound failed", err);
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
    if (this.engine === "recorder") {
      void this._startRecording();
      return;
    }
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
    if (this.engine === "recorder") {
      this._stopRecording(true); // finalize the take and send it for STT
      return;
    }
    if (!this.recognition || !this.listening) return;
    this.recognition.stop();
    this.listening = false;
    this.stateCb(false);
  }

  isListening(): boolean {
    return this.listening || this.recording;
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
    if (!this.supported) return;
    if (this.engine === "recorder") {
      // Click-to-talk: one recorded command per mic press, no auto-listen
      // session (that needs a recognizer that can idle on the mic).
      if (this.recording) return;
      this.playWakeSound();
      void this._startRecording();
      return;
    }
    if (this.sessionActive) return;
    this.sessionActive = true;
    this.playWakeSound(); // Iron Man wake chime
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
    if (this.engine === "recorder") {
      // Second mic click while recording = "I'm done, send it".
      this._stopRecording(true);
      return;
    }
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
      this.playShutdownSound(); // descending power-down chime
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
    // The wake word needs a continuous recognizer — speech engine only.
    return this.engine === "speech";
  }

  isWakeWordEnabled(): boolean {
    return this.wakeEnabled;
  }

  currentStatus(): VoiceStatus {
    if (!this.supported) return "unsupported";
    if (this.engine === "recorder") return this.recording ? "session" : "off";
    if (this.sessionActive) return "session";
    if (this.wakeEnabled) return "wake";
    return "off";
  }

  /** Enable + start the background wake listener. */
  startWakeWord(): void {
    if (this.engine !== "speech") return;
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
    if (this.engine !== "speech") return false;
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

  /** Surface recognition/transcription errors (wired to the HUD by main.ts). */
  onError(cb: (message: string) => void): void {
    this.errorCb = cb;
  }

  // ---- Recorder engine: MediaRecorder → POST /stt → finalCb ----

  private async _startRecording(): Promise<void> {
    if (this.recording) return;

    let stream: MediaStream;
    try {
      stream = await navigator.mediaDevices.getUserMedia({ audio: true });
    } catch (err: any) {
      this.errorCb(
        err?.name === "NotAllowedError"
          ? "Microphone access denied — allow the mic for this site in your " +
              "browser settings."
          : `Could not open the microphone: ${err?.message || err}`
      );
      return;
    }

    // Safari records AAC-in-MP4; Chrome/Firefox record Opus-in-WebM. The
    // backend forwards either container to ElevenLabs untouched.
    const mimeType = ["audio/mp4", "audio/webm;codecs=opus", "audio/webm"].find(
      (t) => (window as any).MediaRecorder.isTypeSupported?.(t)
    );
    let recorder: MediaRecorder;
    try {
      recorder = new MediaRecorder(
        stream,
        mimeType ? { mimeType } : undefined
      );
    } catch (err) {
      stream.getTracks().forEach((t) => t.stop());
      this.errorCb(`Could not start recording: ${err}`);
      return;
    }

    this.recorderChunks = [];
    this.sendOnStop = true;
    recorder.ondataavailable = (e: BlobEvent) => {
      if (e.data && e.data.size > 0) this.recorderChunks.push(e.data);
    };
    recorder.onstop = () => {
      stream.getTracks().forEach((t) => t.stop());
      const blob = new Blob(this.recorderChunks, {
        type: mimeType || "audio/webm",
      });
      this.recorderChunks = [];
      this.recorder = null;
      if (this.sendOnStop && blob.size > 0) void this._transcribe(blob);
    };

    this.recorder = recorder;
    this.recording = true;
    recorder.start();
    this.stateCb(true);
    this._emitStatus();
    this._startSilenceWatch(stream);
  }

  /** Stop the current take; ``send=true`` forwards it for transcription. */
  private _stopRecording(send: boolean): void {
    if (!this.recording || !this.recorder) return;
    this.sendOnStop = send;
    this.recording = false;
    this._stopSilenceWatch();
    try {
      this.recorder.stop(); // fires onstop with the final chunk flushed
    } catch {
      /* already stopped */
    }
    this.stateCb(false);
    this._emitStatus();
  }

  /**
   * Auto-stop the take on trailing silence (or a hard time cap) by watching
   * the stream's RMS level. If the analyser can't be built the recording
   * simply runs until the user clicks the mic again.
   */
  private _startSilenceWatch(stream: MediaStream): void {
    const ctx = this._ensureCtx();
    if (!ctx) return;
    let analyser: AnalyserNode;
    try {
      this.silenceSource = ctx.createMediaStreamSource(stream);
      analyser = ctx.createAnalyser();
      analyser.fftSize = 512;
      this.silenceSource.connect(analyser); // analyser only — no speaker loop
    } catch {
      this.silenceSource = null;
      return;
    }

    const data = new Uint8Array(analyser.fftSize);
    const startedAt = Date.now();
    let lastVoice = 0;

    this.silencePoll = window.setInterval(() => {
      analyser.getByteTimeDomainData(data);
      let sum = 0;
      for (let i = 0; i < data.length; i++) {
        const v = (data[i] - 128) / 128;
        sum += v * v;
      }
      const rms = Math.sqrt(sum / data.length);
      const now = Date.now();
      if (rms > VOICE_RMS_THRESHOLD) lastVoice = now;

      const spoke = lastVoice > 0;
      if (
        now - startedAt >= RECORD_MAX_MS ||
        (spoke && now - lastVoice >= SILENCE_STOP_MS)
      ) {
        this._stopRecording(true);
      } else if (!spoke && now - startedAt >= NO_SPEECH_MS) {
        this._stopRecording(false);
        this.interimCb("No speech detected.");
      }
    }, 120);
  }

  private _stopSilenceWatch(): void {
    if (this.silencePoll !== null) {
      clearInterval(this.silencePoll);
      this.silencePoll = null;
    }
    try {
      this.silenceSource?.disconnect();
    } catch {
      /* already disconnected */
    }
    this.silenceSource = null;
  }

  /** POST the take to the backend STT and feed the transcript to finalCb. */
  private async _transcribe(blob: Blob): Promise<void> {
    this.interimCb("Transcribing…");
    const form = new FormData();
    const ext = blob.type.includes("mp4") ? "mp4" : "webm";
    form.append("file", blob, `command.${ext}`);
    try {
      const res = await fetch(STT_URL, { method: "POST", body: form });
      const data: any = await res.json().catch(() => ({}));
      if (!res.ok || data.error) {
        this.errorCb(
          `Transcription failed: ${data.error || res.statusText || res.status}`
        );
        return;
      }
      const text = (data.text || "").trim();
      if (!text) {
        this.interimCb("No speech detected.");
        return;
      }
      this.finalCb(text); // same path Chrome's recognizer feeds
    } catch (err: any) {
      this.errorCb(
        `Could not reach the transcription service: ${err?.message || err}`
      );
    }
  }
}
