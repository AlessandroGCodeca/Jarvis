import * as THREE from "three";

export type OrbState = "idle" | "listening" | "thinking" | "speaking";

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

/**
 * Audio-reactive particle orb rendered with Three.js.
 *
 * States (idle / listening / thinking / speaking) drive colour, expansion,
 * chaos and rotation; transitions are smoothed with lerp. During listening and
 * speaking, an AnalyserNode feeds frequency amplitude into particle
 * displacement.
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

  private state: OrbState = "idle";
  // Live, lerped parameters.
  private current = {
    color: STATE_PARAMS.idle.color.clone(),
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

      this.randoms[i] = Math.random() * Math.PI * 2;
      this.randoms2[i] = 0.4 + Math.random() * 0.6;
    }

    this.geometry = new THREE.BufferGeometry();
    this.geometry.setAttribute(
      "position",
      new THREE.BufferAttribute(positions, 3)
    );

    this.material = new THREE.PointsMaterial({
      color: this.current.color.clone(),
      size: 0.035,
      transparent: true,
      opacity: 0.9,
      blending: THREE.AdditiveBlending,
      depthWrite: false,
      sizeAttenuation: true,
    });

    this.points = new THREE.Points(this.geometry, this.material);
    this.scene.add(this.points);

    // Web Audio setup — created lazily-resumable on first user gesture.
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

    // Smoothly approach target parameters.
    this.current.expansion +=
      (target.expansion - this.current.expansion) * lerp;
    this.current.chaos += (target.chaos - this.current.chaos) * lerp;
    this.current.rotationSpeed +=
      (target.rotationSpeed - this.current.rotationSpeed) * lerp;
    this.current.pulseSpeed +=
      (target.pulseSpeed - this.current.pulseSpeed) * lerp;
    this.current.color.lerp(target.color, lerp);
    this.material.color.copy(this.current.color);

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

    const pos = this.geometry.attributes.position.array as Float32Array;
    for (let i = 0; i < PARTICLE_COUNT; i++) {
      const idx = i * 3;
      const phase = this.randoms[i];
      const noise = Math.sin(t * this.current.pulseSpeed * 1.7 + phase);
      const factor =
        this.current.expansion * pulse +
        noise * this.current.chaos +
        ampBoost * this.randoms2[i] * 0.6;

      pos[idx] = this.basePositions[idx] * factor;
      pos[idx + 1] = this.basePositions[idx + 1] * factor;
      pos[idx + 2] = this.basePositions[idx + 2] * factor;
    }
    this.geometry.attributes.position.needsUpdate = true;

    this.points.rotation.y += this.current.rotationSpeed;
    this.points.rotation.x += this.current.rotationSpeed * 0.3;

    this.renderer.render(this.scene, this.camera);
  };
}
