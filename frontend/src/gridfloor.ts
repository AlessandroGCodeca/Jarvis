/**
 * Animated perspective "floor" grid below the orb.
 *
 * Canvas-drawn: vertical lines converge to a vanishing point at top-center,
 * horizontal lines scroll toward the viewer on a 4s loop (slower when idle).
 * While JARVIS speaks the grid pulses brighter with the TTS amplitude, read
 * from the orb's shared analyser. Edge fade is applied via a CSS mask.
 */

const WIDTH = 600;
const HEIGHT = 200;
const V_LINES = 12;
const H_LINES = 8;
const BASE_ALPHA = 0.08;
const SPEAK_ALPHA = 0.15; // peak alpha while speaking at full amplitude
const SCROLL_SPEED = 1 / 4; // cycles per second (4s loop)
const IDLE_SCROLL_SPEED = 0.05; // barely visible drift when idle

export class GridFloor {
  private ctx: CanvasRenderingContext2D | null;
  private freqData: Uint8Array<ArrayBuffer>;
  private amplitude = 0; // smoothed 0..1
  private scroll = 0; // 0..1 phase of the scroll loop
  private lastTime = performance.now();

  constructor(
    canvas: HTMLCanvasElement,
    private analyser: AnalyserNode
  ) {
    canvas.width = WIDTH;
    canvas.height = HEIGHT;
    this.ctx = canvas.getContext("2d");
    this.freqData = new Uint8Array(
      new ArrayBuffer(this.analyser.frequencyBinCount)
    );
    requestAnimationFrame(this.draw);
  }

  private draw = (now: number): void => {
    requestAnimationFrame(this.draw);
    const ctx = this.ctx;
    if (!ctx) return;

    const dt = Math.min((now - this.lastTime) / 1000, 0.1);
    this.lastTime = now;

    const body = document.body.classList;
    const speaking = body.contains("act-speaking");
    const idle = body.contains("act-standby") || body.contains("act-offline");

    this.scroll =
      (this.scroll + dt * (idle ? IDLE_SCROLL_SPEED : SCROLL_SPEED)) % 1;

    // Audio amplitude only matters while speaking; decay otherwise.
    if (speaking) {
      this.analyser.getByteFrequencyData(this.freqData);
      let sum = 0;
      for (let i = 0; i < this.freqData.length; i++) sum += this.freqData[i];
      const avg = sum / this.freqData.length / 255;
      this.amplitude += (avg - this.amplitude) * 0.3;
    } else {
      this.amplitude += (0 - this.amplitude) * 0.1;
    }

    const alpha =
      BASE_ALPHA + (speaking ? this.amplitude * (SPEAK_ALPHA - BASE_ALPHA) * 2 : 0);

    ctx.clearRect(0, 0, WIDTH, HEIGHT);
    ctx.lineWidth = 1;
    const vpx = WIDTH / 2; // vanishing point at top-center

    // Vertical lines fanning out from the vanishing point.
    ctx.strokeStyle = `rgba(0, 180, 255, ${Math.min(alpha, 0.3)})`;
    for (let i = 0; i < V_LINES; i++) {
      // Bottom anchors spread wider than the canvas so edge lines exit the
      // sides, selling the floor-plane illusion.
      const xBottom = (i / (V_LINES - 1)) * WIDTH * 1.6 - WIDTH * 0.3;
      ctx.beginPath();
      ctx.moveTo(vpx, 0);
      ctx.lineTo(xBottom, HEIGHT);
      ctx.stroke();
    }

    // Horizontal lines with perspective spacing, scrolling toward the viewer.
    for (let i = 0; i < H_LINES; i++) {
      const d = (i + this.scroll) / H_LINES; // 0 (horizon) .. 1 (near)
      const y = HEIGHT * d * d; // quadratic: denser near the horizon
      const half = WIDTH * 0.8 * d + 2;
      // Nearer lines are brighter, like the verticals' fade toward the VP.
      const lineAlpha = Math.min(alpha * (0.25 + 0.75 * d), 0.3);
      ctx.strokeStyle = `rgba(0, 180, 255, ${lineAlpha})`;
      ctx.beginPath();
      ctx.moveTo(vpx - half, y);
      ctx.lineTo(vpx + half, y);
      ctx.stroke();
    }
  };
}
