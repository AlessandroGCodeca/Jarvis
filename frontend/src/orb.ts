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

/**
 * Audio-reactive particle orb rendered with Three.js.
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
  private points: THREE.Points;
  private geometry: THREE.BufferGeometry;
  private material: THREE.PointsMaterial;

  private basePositions: Float32Array; // unit-sphere fibonacci positions
  private randoms: Float32Array; // per-particle random phase
  private randoms2: Float32Array; // per-particle random amplitude weight
  private colorArray: Float32Array; // per-particle RGB

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

  // ---- Web Audio (shared context for playback + reactivity) ----
  private audioCtx: AudioContext;
  private analyser: AnalyserNode;
  private freqData: Uint8Array<ArrayBuffer>;
  private amplitude = 0; // smoothed 0..1

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
    this.camera.position.z = 5;

    // Build a fibonacci-sphere distribution of particles.
    this.basePositions = new Float32Array(PARTICLE_COUNT * 3);
    this.randoms = new Float32Array(PARTICLE_COUNT);
    this.randoms2 = new Float32Array(PARTICLE_COUNT);
    this.colorArray = new Float32Array(PARTICLE_COUNT * 3);
    const positions = new Float32Array(PARTICLE_COUNT * 3);
    const golden = Math.PI * (3 - Math.sqrt(5)); // golden angle

    for (let i = 0; i < PARTICLE_COUNT; i++) {
      const y = 1 - (i / (PARTICLE_COUNT - 1)) * 2; // 1 .. -1
      const radius = Math.sqrt(Math.max(0, 1 - y * y));
      const theta = golden * i;
      const x = Math.cos(theta) * radius;
      const z = Math.sin(theta) * radius;

      const idx = i * 3;
      this.basePositions[idx] = x;
      this.basePositions[idx + 1] = y;
      this.basePositions[idx + 2] = z;
      positions[idx] = x;
      positions[idx + 1] = y;
      positions[idx + 2] = z;

      this.colorArray[idx] = this.curColor.r;
      this.colorArray[idx + 1] = this.curColor.g;
      this.colorArray[idx + 2] = this.curColor.b;

      this.randoms[i] = Math.random() * Math.PI * 2;
      this.randoms2[i] = 0.4 + Math.random() * 0.6;
    }

    this.geometry = new THREE.BufferGeometry();
    this.geometry.setAttribute(
      "position",
      new THREE.BufferAttribute(positions, 3)
    );
    this.geometry.setAttribute(
      "color",
      new THREE.BufferAttribute(this.colorArray, 3)
    );

    this.material = new THREE.PointsMaterial({
      size: 0.035,
      transparent: true,
      opacity: 0.9,
      blending: THREE.AdditiveBlending,
      depthWrite: false,
      sizeAttenuation: true,
      vertexColors: true, // colour driven per-particle for gradient support
    });

    this.points = new THREE.Points(this.geometry, this.material);
    this.scene.add(this.points);

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

  /** The shared AudioContext, so callers can decode audio into the same graph. */
  get audioContext(): AudioContext {
    return this.audioCtx;
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

  /** Play a decoded buffer (TTS) through the analyser; orb reacts + returns to idle. */
  playAudioBuffer(buffer: AudioBuffer): void {
    this.resume();
    const source = this.audioCtx.createBufferSource();
    source.buffer = buffer;
    source.connect(this.analyser);
    this.analyser.connect(this.audioCtx.destination);
    this.setState("speaking");
    source.onended = () => {
      this.setState("idle");
    };
    source.start();
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

    const t = this.clock.getElapsedTime();
    const pulse = 1 + Math.sin(t * this.current.pulseSpeed) * 0.03;
    const ampBoost = this.amplitude * (this.state === "speaking" ? 1.0 : 0.5);

    const cr = this.curColor.r;
    const cg = this.curColor.g;
    const cb = this.curColor.b;
    const ga = this.gradientAmount;

    const pos = this.geometry.attributes.position.array as Float32Array;
    const col = this.colorArray;
    for (let i = 0; i < PARTICLE_COUNT; i++) {
      const idx = i * 3;
      const baseY = this.basePositions[idx + 1];
      const phase = this.randoms[i];
      const noise = Math.sin(t * this.current.pulseSpeed * 1.7 + phase);
      const factor =
        this.current.expansion * pulse +
        noise * this.current.chaos +
        ampBoost * this.randoms2[i] * 0.6;

      pos[idx] = this.basePositions[idx] * factor;
      pos[idx + 1] = baseY * factor;
      pos[idx + 2] = this.basePositions[idx + 2] * factor;

      // Per-particle colour: uniform curColor, optionally blended into a
      // top(gold)→bottom(orange) sunrise gradient by gradientAmount.
      if (ga > 0.001) {
        const tY = (baseY + 1) * 0.5; // 0 bottom .. 1 top
        const gr = GRAD_BOTTOM.r + (GRAD_TOP.r - GRAD_BOTTOM.r) * tY;
        const gg = GRAD_BOTTOM.g + (GRAD_TOP.g - GRAD_BOTTOM.g) * tY;
        const gb = GRAD_BOTTOM.b + (GRAD_TOP.b - GRAD_BOTTOM.b) * tY;
        col[idx] = cr + (gr - cr) * ga;
        col[idx + 1] = cg + (gg - cg) * ga;
        col[idx + 2] = cb + (gb - cb) * ga;
      } else {
        col[idx] = cr;
        col[idx + 1] = cg;
        col[idx + 2] = cb;
      }
    }
    this.geometry.attributes.position.needsUpdate = true;
    this.geometry.attributes.color.needsUpdate = true;

    this.points.rotation.y += this.current.rotationSpeed;
    this.points.rotation.x += this.current.rotationSpeed * 0.3;

    this.renderer.render(this.scene, this.camera);
  };
}
