/**
 * Horizontal waveform visualizer drawn below the orb.
 *
 * Reads the orb's shared AnalyserNode (fed by the mic while listening and by
 * TTS playback while speaking) and draws a symmetric, mirrored waveform centred
 * on the midline. THINKING shows a cosmetic sine wave (no real audio); IDLE is a
 * faint flat line. The whole thing fades in/out with the active state.
 */

export type WaveState = "idle" | "listening" | "thinking" | "speaking";

const COLORS: Record<WaveState, string> = {
  idle: "0, 212, 255",
  listening: "0, 255, 136",
  thinking: "124, 58, 237",
  speaking: "0, 212, 255",
};

export class Waveform {
  private canvas: HTMLCanvasElement;
  private ctx: CanvasRenderingContext2D;
  private analyser: AnalyserNode | null = null;
  private freq: Uint8Array<ArrayBuffer>;
  private state: WaveState = "idle";
  private opacity = 0; // eased toward 1 when active, 0 when idle
  private dpr: number;
  private w = 400;
  private h = 48;
  private phase = 0; // for the cosmetic thinking sine

  constructor(canvas: HTMLCanvasElement, analyser: AnalyserNode | null = null) {
    this.canvas = canvas;
    this.ctx = canvas.getContext("2d") as CanvasRenderingContext2D;
    this.analyser = analyser;
    this.freq = new Uint8Array(
      new ArrayBuffer(analyser ? analyser.frequencyBinCount : 128)
    );
    this.dpr = Math.min(window.devicePixelRatio || 1, 2);
    this.resize();
    requestAnimationFrame(this.draw);
  }

  setAnalyser(analyser: AnalyserNode): void {
    this.analyser = analyser;
    this.freq = new Uint8Array(new ArrayBuffer(analyser.frequencyBinCount));
  }

  setState(state: WaveState): void {
    this.state = state;
  }

  private resize(): void {
    this.canvas.width = Math.floor(this.w * this.dpr);
    this.canvas.height = Math.floor(this.h * this.dpr);
    this.canvas.style.width = `${this.w}px`;
    this.canvas.style.height = `${this.h}px`;
    this.ctx.setTransform(this.dpr, 0, 0, this.dpr, 0, 0);
  }

  private draw = (): void => {
    const active = this.state !== "idle";
    const target = active ? 1 : 0;
    this.opacity += (target - this.opacity) * 0.12;

    const ctx = this.ctx;
    const w = this.w;
    const h = this.h;
    const mid = h / 2;
    ctx.clearRect(0, 0, w, h);

    const rgb = COLORS[this.state];

    // Faint flat midline when essentially idle.
    if (this.opacity < 0.02) {
      ctx.strokeStyle = `rgba(${COLORS.idle}, 0.2)`;
      ctx.lineWidth = 1;
      ctx.beginPath();
      ctx.moveTo(0, mid);
      ctx.lineTo(w, mid);
      ctx.stroke();
      requestAnimationFrame(this.draw);
      return;
    }

    // Build a normalized amplitude array (0..1) across the width.
    const points = 64;
    const amps = new Array<number>(points);
    if (this.state === "thinking") {
      this.phase += 0.05;
      for (let i = 0; i < points; i++) {
        const x = i / (points - 1);
        amps[i] =
          (Math.sin(x * Math.PI * 4 + this.phase) * 0.5 + 0.5) * 0.5 * 0.6;
      }
    } else if (this.analyser) {
      this.analyser.getByteFrequencyData(this.freq);
      const bins = this.freq.length;
      for (let i = 0; i < points; i++) {
        // Sample the lower ~70% of the spectrum (more musical/voice energy).
        const idx = Math.floor((i / points) * bins * 0.7);
        amps[i] = (this.freq[idx] / 255) * 0.9;
      }
    } else {
      amps.fill(0);
    }

    // Mirror top + bottom around the midline as a smooth filled shape.
    const step = w / (points - 1);
    ctx.lineWidth = 1.5;
    ctx.strokeStyle = `rgba(${rgb}, ${this.opacity})`;
    ctx.fillStyle = `rgba(${rgb}, ${this.opacity * 0.12})`;

    for (const dir of [-1, 1]) {
      ctx.beginPath();
      ctx.moveTo(0, mid);
      for (let i = 0; i < points; i++) {
        const x = i * step;
        const y = mid + dir * amps[i] * (mid - 2);
        ctx.lineTo(x, y);
      }
      ctx.lineTo(w, mid);
      ctx.stroke();
      ctx.fill();
    }

    requestAnimationFrame(this.draw);
  };
}
