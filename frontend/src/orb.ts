import * as THREE from "three";

export type OrbState = "idle" | "listening" | "thinking" | "speaking";

export type Mood =
  | "neutral"
  | "happy"
  | "positive"
  | "thinking"
  | "weather"
  | "music"
  | "email"
  | "calendar"
  | "error"
  | "warning"
  | "morning_briefing";

interface StateParams {
  color: THREE.Color;
  expansion: number; // base radius multiplier
  chaos: number; // amount of per-particle noise displacement
  rotationSpeed: number; // radians per frame
  pulseSpeed: number; // breathing speed
}

const PARTICLE_COUNT = 2000;

const STATE_PARAMS: Record<OrbState, StateParams> = {
  idle: {
    color: new THREE.Color(0x1e6fff),
    expansion: 1.0,
    chaos: 0.04,
    rotationSpeed: 0.0008,
    pulseSpeed: 0.8,
  },
  listening: {
    color: new THREE.Color(0x00d4ff),
    expansion: 1.32,
    chaos: 0.08,
    rotationSpeed: 0.0015,
    pulseSpeed: 1.4,
  },
  thinking: {
    color: new THREE.Color(0x7c3aed),
    expansion: 1.12,
    chaos: 0.12,
    rotationSpeed: 0.006,
    pulseSpeed: 2.2,
  },
  speaking: {
    color: new THREE.Color(0x00e5ff),
    expansion: 1.25,
    chaos: 0.22,
    rotationSpeed: 0.0025,
    pulseSpeed: 3.0,
  },
};

// Mood -> particle colour.
const MOOD_COLORS: Record<Mood, number> = {
  neutral: 0x00d4ff,
  happy: 0xffd700,
  positive: 0xffd700,
  thinking: 0x7c3aed,
  weather: 0x87ceeb,
  music: 0x00ff88,
  email: 0xff8c00,
  calendar: 0xff8c00,
  error: 0xff4444,
  warning: 0xff4444,
  morning_briefing: 0xffd700, // gold; blended with orange as a gradient
};

// Sunrise gradient endpoints for the morning briefing.
const GRAD_TOP = new THREE.Color(0xffd700); // gold (top)
const GRAD_BOTTOM = new THREE.Color(0xff8c00); // orange (bottom)

// Depth fog: particles at the back fade to FOG_MIN, at the front FOG_MAX.
const FOG_MIN = 0.2;
const FOG_MAX = 1.0;

/** White circle on a transparent background, for round particle sprites. */
function makeCircleTexture(): THREE.Texture {
  const canvas = document.createElement("canvas");
  canvas.width = 16;
  canvas.height = 16;
  const ctx = canvas.getContext("2d");
  if (ctx) {
    ctx.fillStyle = "#ffffff";
    ctx.beginPath();
    ctx.arc(8, 8, 7, 0, Math.PI * 2);
    ctx.fill();
  }
  return new THREE.CanvasTexture(canvas);
}

/** One of the three layered particle systems (squares / circles / dim bg). */
interface ParticleSystem {
  geometry: THREE.BufferGeometry;
  points: THREE.Points;
  basePositions: Float32Array; // unit-sphere fibonacci positions
  randoms: Float32Array; // per-particle random phase
  randoms2: Float32Array; // per-particle random amplitude weight
  colors: Float32Array; // per-particle RGB (fog-attenuated)
  count: number;
}

/**
 * Audio-reactive particle orb rendered with Three.js.
 *
 * Three layered particle systems share one fibonacci-sphere distribution:
 * ~60% squares (the classic look), ~25% round sprites, and ~15% larger dim
 * background particles for depth. All react to state changes together. A
 * per-particle depth fog dims particles at the back of the sphere (additive
 * blending, so scaling the colour is equivalent to scaling alpha).
 *
 * States (idle / listening / thinking / speaking) drive motion (colour too,
 * when no mood is set). `setMood()` overrides the colour based on the mood of
 * JARVIS's response and smoothly lerps to it over ~1s; "morning_briefing"
 * renders a gold→orange sunrise gradient across the sphere.
 */
export class OrbVisualizer {
  private renderer: THREE.WebGLRenderer;
  private scene: THREE.Scene;
  private camera: THREE.PerspectiveCamera;
  private group: THREE.Group;
  private systems: ParticleSystem[] = [];

  private state: OrbState = "idle";
  private moodLock = false; // when set, mood colour overrides state colour
  private targetColor = new THREE.Color(STATE_PARAMS.idle.color);
  private curColor = new THREE.Color(STATE_PARAMS.idle.color);
  private gradientAmount = 0; // 0 = uniform, 1 = full sunrise gradient
  private targetGradientAmount = 0;

  // Motion params, lerped toward the active state.
  private current = {
    expansion: STATE_PARAMS.idle.expansion,
    chaos: STATE_PARAMS.idle.chaos,
    rotationSpeed: STATE_PARAMS.idle.rotationSpeed,
    pulseSpeed: STATE_PARAMS.idle.pulseSpeed,
  };

  private clock = new THREE.Clock();

  // Idle "breathing": eased toward 1 when idle, 0 otherwise, scaling a slow
  // 4-second sinusoid so the resting orb gently expands and contracts.
  private breathingAmount = 1;

  // ---- Web Audio (shared context for playback + reactivity) ----
  private audioCtx: AudioContext;
  private analyser: AnalyserNode;
  private freqData: Uint8Array<ArrayBuffer>;
  private amplitude = 0; // smoothed 0..1

  // ---- Playback queue (sentence-chunked TTS) ----
  private audioQueue: AudioBuffer[] = [];
  private currentSource: AudioBufferSourceNode | null = null;
  private isPlayingQueue = false;
  private streamEnded = false; // backend signalled all chunks for this turn
  private notifiedSpeaking = false; // last value sent to speakingChangeCb
  private playbackCompleteCb: () => void = () => {};
  private speakingChangeCb: (speaking: boolean) => void = () => {};

  constructor(canvas: HTMLCanvasElement) {
    this.renderer = new THREE.WebGLRenderer({
      canvas,
      alpha: true,
      antialias: true,
    });
    this.renderer.setPixelRatio(Math.min(window.devicePixelRatio, 2));
    this.renderer.setSize(window.innerWidth, window.innerHeight);

    this.scene = new THREE.Scene();
    this.camera = new THREE.PerspectiveCamera(
      60,
      window.innerWidth / window.innerHeight,
      0.1,
      100
    );
    // Camera pulled in (was 5) so the particle sphere renders ~40% larger.
    this.camera.position.z = 3.57;

    this.group = new THREE.Group();
    this.scene.add(this.group);
    this.buildParticleSystems();

    // Web Audio setup — resumed on first user gesture.
    const Ctx =
      (window as any).AudioContext || (window as any).webkitAudioContext;
    this.audioCtx = new Ctx();
    this.analyser = this.audioCtx.createAnalyser();
    this.analyser.fftSize = 256;
    this.analyser.smoothingTimeConstant = 0.8;
    this.freqData = new Uint8Array(new ArrayBuffer(this.analyser.frequencyBinCount));

    window.addEventListener("resize", this.onResize);
    this.animate();
  }

  /** Build the three layered systems over one fibonacci-sphere distribution. */
  private buildParticleSystems(): void {
    // Interleave assignment (i % 20) so each system samples the whole sphere
    // evenly — contiguous index ranges would slice it into latitude bands.
    const systemFor = (i: number): number => {
      const m = i % 20;
      if (m < 12) return 0; // 60% squares
      if (m < 17) return 1; // 25% circles
      return 2; // 15% large dim background
    };

    const counts = [0, 0, 0];
    for (let i = 0; i < PARTICLE_COUNT; i++) counts[systemFor(i)]++;

    const materials = [
      // System 1: the classic squares (default Points sprite).
      new THREE.PointsMaterial({
        size: 0.035,
        transparent: true,
        opacity: 0.9,
        blending: THREE.AdditiveBlending,
        depthWrite: false,
        sizeAttenuation: true,
        vertexColors: true,
      }),
      // System 2: round sprites via a circular canvas texture.
      new THREE.PointsMaterial({
        size: 0.04,
        map: makeCircleTexture(),
        alphaTest: 0.5,
        transparent: true,
        opacity: 0.9,
        blending: THREE.AdditiveBlending,
        depthWrite: false,
        sizeAttenuation: true,
        vertexColors: true,
      }),
      // System 3: larger, dim background particles for depth.
      new THREE.PointsMaterial({
        size: 0.14, // 4x the base size
        transparent: true,
        opacity: 0.15,
        blending: THREE.AdditiveBlending,
        depthWrite: false,
        sizeAttenuation: true,
        vertexColors: true,
      }),
    ];

    this.systems = counts.map((count, s) => {
      const geometry = new THREE.BufferGeometry();
      const positions = new Float32Array(count * 3);
      const colors = new Float32Array(count * 3);
      geometry.setAttribute(
        "position",
        new THREE.BufferAttribute(positions, 3)
      );
      geometry.setAttribute("color", new THREE.BufferAttribute(colors, 3));
      const points = new THREE.Points(geometry, materials[s]);
      this.group.add(points);
      return {
        geometry,
        points,
        basePositions: new Float32Array(count * 3),
        randoms: new Float32Array(count),
        randoms2: new Float32Array(count),
        colors,
        count,
      };
    });

    // One fibonacci-sphere distribution, routed across the three systems.
    const golden = Math.PI * (3 - Math.sqrt(5)); // golden angle
    const cursor = [0, 0, 0];
    for (let i = 0; i < PARTICLE_COUNT; i++) {
      const y = 1 - (i / (PARTICLE_COUNT - 1)) * 2; // 1 .. -1
      const radius = Math.sqrt(Math.max(0, 1 - y * y));
      const theta = golden * i;
      const x = Math.cos(theta) * radius;
      const z = Math.sin(theta) * radius;

      const s = systemFor(i);
      const sys = this.systems[s];
      const j = cursor[s]++;
      const idx = j * 3;
      sys.basePositions[idx] = x;
      sys.basePositions[idx + 1] = y;
      sys.basePositions[idx + 2] = z;
      (sys.geometry.attributes.position.array as Float32Array)[idx] = x;
      (sys.geometry.attributes.position.array as Float32Array)[idx + 1] = y;
      (sys.geometry.attributes.position.array as Float32Array)[idx + 2] = z;
      sys.colors[idx] = this.curColor.r;
      sys.colors[idx + 1] = this.curColor.g;
      sys.colors[idx + 2] = this.curColor.b;
      sys.randoms[j] = Math.random() * Math.PI * 2;
      sys.randoms2[j] = 0.4 + Math.random() * 0.6;
    }
  }

  /** The shared AudioContext, so callers can decode audio into the same graph. */
  get audioContext(): AudioContext {
    return this.audioCtx;
  }

  /** The shared analyser (fed by the mic when listening and TTS when speaking),
   *  so the waveform visualizer can read the same audio data the orb reacts to. */
  getAnalyser(): AnalyserNode {
    return this.analyser;
  }

  /** Browsers require a user gesture before audio can play; call on click. */
  resume(): void {
    if (this.audioCtx.state === "suspended") {
      void this.audioCtx.resume();
    }
  }

  setState(state: OrbState): void {
    this.state = state;
    // When no mood is locked, colour follows the state.
    if (!this.moodLock) {
      this.targetColor.copy(STATE_PARAMS[state].color);
    }
  }

  /** Set the orb colour from the mood of a response. Lerps over ~1s. */
  setMood(mood: Mood): void {
    if (mood === "neutral") {
      this.moodLock = false;
      this.targetGradientAmount = 0;
      this.targetColor.copy(STATE_PARAMS[this.state].color);
      return;
    }
    if (mood === "morning_briefing") {
      this.moodLock = true;
      this.targetGradientAmount = 1; // sunrise gradient
      this.targetColor.setHex(MOOD_COLORS.morning_briefing);
      return;
    }
    this.moodLock = true;
    this.targetGradientAmount = 0;
    this.targetColor.setHex(MOOD_COLORS[mood] ?? MOOD_COLORS.neutral);
  }

  /** Tap a microphone stream into the analyser for listening reactivity. */
  connectAudio(stream: MediaStream): void {
    try {
      const src = this.audioCtx.createMediaStreamSource(stream);
      src.connect(this.analyser);
    } catch (err) {
      console.warn("connectAudio failed", err);
    }
  }

  /** Called once a turn's audio has fully finished playing (or none arrived). */
  setPlaybackCompleteHandler(cb: () => void): void {
    this.playbackCompleteCb = cb;
  }

  /** Notified when the orb starts (true) or stops (false) speaking. */
  onSpeakingChange(cb: (speaking: boolean) => void): void {
    this.speakingChangeCb = cb;
  }

  /** True while TTS audio is playing or queued. */
  isSpeaking(): boolean {
    return this.isPlayingQueue || this.audioQueue.length > 0;
  }

  /** Emit a speaking transition only when it actually changes. */
  private _setSpeaking(speaking: boolean): void {
    if (this.notifiedSpeaking === speaking) return;
    this.notifiedSpeaking = speaking;
    this.speakingChangeCb(speaking);
  }

  /** Queue a decoded TTS buffer; plays immediately if nothing else is playing. */
  enqueueAudio(buffer: AudioBuffer): void {
    this.resume();
    this.audioQueue.push(buffer);
    this._playNext();
  }

  /** Backend signalled all audio chunks for this turn have been sent. */
  markAudioStreamEnd(): void {
    this.streamEnded = true;
    if (!this.isPlayingQueue && this.audioQueue.length === 0) {
      this._completePlayback();
    }
  }

  /** Stop playback immediately and clear the queue (barge-in / interrupt). */
  stopPlayback(): void {
    this.audioQueue = [];
    this.streamEnded = false;
    if (this.currentSource) {
      try {
        this.currentSource.onended = null;
        this.currentSource.stop();
      } catch {
        /* already stopped */
      }
      this.currentSource = null;
    }
    this.isPlayingQueue = false;
    this.setState("idle");
    this._setSpeaking(false);
  }

  /** Play the next queued buffer (TTS) through the analyser for reactivity. */
  private _playNext(): void {
    if (this.isPlayingQueue) return;
    const buffer = this.audioQueue.shift();
    if (!buffer) {
      // Queue drained; if the backend is done sending, end the turn.
      if (this.streamEnded) this._completePlayback();
      return;
    }

    this.isPlayingQueue = true;
    this.setState("speaking");
    this._setSpeaking(true);

    const source = this.audioCtx.createBufferSource();
    source.buffer = buffer;
    // Route to the speakers (to hear it) AND the analyser (for reactivity).
    // The analyser is intentionally NOT connected to the destination, so a
    // live mic tapped into it (connectAudio) never echoes back to the output.
    source.connect(this.audioCtx.destination);
    source.connect(this.analyser);
    this.currentSource = source;
    source.onended = () => {
      if (this.currentSource === source) this.currentSource = null;
      this.isPlayingQueue = false;
      if (this.audioQueue.length > 0) {
        this._playNext();
      } else if (this.streamEnded) {
        this._completePlayback();
      }
      // Otherwise: waiting for the next chunk to arrive.
    };
    source.start();
  }

  /** Settle the orb and notify that the turn's audio is fully done. */
  private _completePlayback(): void {
    this.streamEnded = false;
    this.isPlayingQueue = false;
    this.setState("idle");
    this._setSpeaking(false);
    this.playbackCompleteCb();
  }

  private onResize = (): void => {
    this.camera.aspect = window.innerWidth / window.innerHeight;
    this.camera.updateProjectionMatrix();
    this.renderer.setSize(window.innerWidth, window.innerHeight);
  };

  private animate = (): void => {
    requestAnimationFrame(this.animate);

    const target = STATE_PARAMS[this.state];
    const lerp = 0.06;

    // Smoothly approach target motion params.
    this.current.expansion +=
      (target.expansion - this.current.expansion) * lerp;
    this.current.chaos += (target.chaos - this.current.chaos) * lerp;
    this.current.rotationSpeed +=
      (target.rotationSpeed - this.current.rotationSpeed) * lerp;
    this.current.pulseSpeed +=
      (target.pulseSpeed - this.current.pulseSpeed) * lerp;

    // Colour transitions over ~1s (≈0.03/frame at 60fps).
    const colorLerp = 0.03;
    this.curColor.lerp(this.targetColor, colorLerp);
    this.gradientAmount +=
      (this.targetGradientAmount - this.gradientAmount) * colorLerp;

    // Read audio amplitude when it matters.
    if (this.state === "speaking" || this.state === "listening") {
      this.analyser.getByteFrequencyData(this.freqData);
      let sum = 0;
      for (let i = 0; i < this.freqData.length; i++) sum += this.freqData[i];
      const avg = sum / this.freqData.length / 255; // 0..1
      this.amplitude += (avg - this.amplitude) * 0.3;
    } else {
      this.amplitude += (0 - this.amplitude) * 0.1;
    }

    // Ease the breathing amount toward 1 only when idle.
    const breatheTarget = this.state === "idle" ? 1 : 0;
    this.breathingAmount += (breatheTarget - this.breathingAmount) * lerp;

    const t = this.clock.getElapsedTime();
    const pulse = 1 + Math.sin(t * this.current.pulseSpeed) * 0.03;
    // Slow 4s breath (period = 2π/1.5708), ±0.05 scale, idle only.
    const breathe = Math.sin(t * 1.5708) * 0.05 * this.breathingAmount;
    const ampBoost = this.amplitude * (this.state === "speaking" ? 1.0 : 0.5);

    const cr = this.curColor.r;
    const cg = this.curColor.g;
    const cb = this.curColor.b;
    const ga = this.gradientAmount;

    // Depth fog: rotate each particle into view space (group rotation order
    // XYZ with z=0 → apply Y, then X) and dim by camera-space depth.
    const ry = this.group.rotation.y;
    const rx = this.group.rotation.x;
    const cosY = Math.cos(ry);
    const sinY = Math.sin(ry);
    const cosX = Math.cos(rx);
    const sinX = Math.sin(rx);
    // Approximate current sphere radius for normalizing depth to -1..1.
    const radius =
      (this.current.expansion + breathe) * pulse + ampBoost * 0.4 + 0.0001;

    for (const sys of this.systems) {
      const pos = sys.geometry.attributes.position.array as Float32Array;
      const col = sys.colors;
      for (let i = 0; i < sys.count; i++) {
        const idx = i * 3;
        const baseY = sys.basePositions[idx + 1];
        const phase = sys.randoms[i];
        const noise = Math.sin(t * this.current.pulseSpeed * 1.7 + phase);
        const factor =
          (this.current.expansion + breathe) * pulse +
          noise * this.current.chaos +
          ampBoost * sys.randoms2[i] * 0.6;

        const x = sys.basePositions[idx] * factor;
        const y = baseY * factor;
        const z = sys.basePositions[idx + 2] * factor;
        pos[idx] = x;
        pos[idx + 1] = y;
        pos[idx + 2] = z;

        // View-space depth (camera looks down -z, so +z faces the viewer).
        const z1 = -sinY * x + cosY * z;
        const zView = sinX * y + cosX * z1;
        let nz = zView / radius;
        if (nz > 1) nz = 1;
        else if (nz < -1) nz = -1;
        const fog = FOG_MIN + ((nz + 1) * 0.5) * (FOG_MAX - FOG_MIN);

        // Per-particle colour: uniform curColor, optionally blended into a
        // top(gold)→bottom(orange) sunrise gradient, then fog-attenuated
        // (additive blending makes colour scaling equivalent to alpha).
        let r = cr;
        let g = cg;
        let b = cb;
        if (ga > 0.001) {
          const tY = (baseY + 1) * 0.5; // 0 bottom .. 1 top
          const gr = GRAD_BOTTOM.r + (GRAD_TOP.r - GRAD_BOTTOM.r) * tY;
          const gg = GRAD_BOTTOM.g + (GRAD_TOP.g - GRAD_BOTTOM.g) * tY;
          const gb = GRAD_BOTTOM.b + (GRAD_TOP.b - GRAD_BOTTOM.b) * tY;
          r = cr + (gr - cr) * ga;
          g = cg + (gg - cg) * ga;
          b = cb + (gb - cb) * ga;
        }
        col[idx] = r * fog;
        col[idx + 1] = g * fog;
        col[idx + 2] = b * fog;
      }
      sys.geometry.attributes.position.needsUpdate = true;
      sys.geometry.attributes.color.needsUpdate = true;
    }

    this.group.rotation.y += this.current.rotationSpeed;
    this.group.rotation.x += this.current.rotationSpeed * 0.3;

    this.renderer.render(this.scene, this.camera);
  };
}
