/**
 * Drifting star field rendered on a 2D canvas behind the orb.
 *
 * 150 stars slowly drift and wrap around the viewport edges; every ~8s a few
 * stars briefly "flare" (brighten + grow, then fade over ~1s). Pure canvas/JS,
 * no dependencies.
 */

interface Star {
  x: number;
  y: number;
  r: number;
  baseOpacity: number;
  vx: number;
  vy: number;
  flare: number; // 1 -> 0 during a flare, 0 when idle
}

const STAR_COUNT = 150;

export function initStarfield(canvas: HTMLCanvasElement): void {
  const ctx = canvas.getContext("2d");
  if (!ctx) return;

  const dpr = Math.min(window.devicePixelRatio || 1, 2);
  let w = 0;
  let h = 0;

  const resize = (): void => {
    w = window.innerWidth;
    h = window.innerHeight;
    canvas.width = Math.floor(w * dpr);
    canvas.height = Math.floor(h * dpr);
    canvas.style.width = `${w}px`;
    canvas.style.height = `${h}px`;
    ctx.setTransform(dpr, 0, 0, dpr, 0, 0);
  };

  const makeStar = (): Star => ({
    x: Math.random() * Math.max(w, 1),
    y: Math.random() * Math.max(h, 1),
    r: 0.5 + Math.random(), // 0.5 .. 1.5
    baseOpacity: 0.3 + Math.random() * 0.5, // 0.3 .. 0.8
    vx: (Math.random() - 0.5) * 0.1, // -0.05 .. 0.05 px/frame
    vy: (Math.random() - 0.5) * 0.1,
    flare: 0,
  });

  resize();
  const stars: Star[] = Array.from({ length: STAR_COUNT }, makeStar);
  window.addEventListener("resize", resize);

  // Every 8s, flare 2-3 random stars.
  window.setInterval(() => {
    const count = 2 + Math.floor(Math.random() * 2);
    for (let i = 0; i < count; i++) {
      stars[Math.floor(Math.random() * stars.length)].flare = 1;
    }
  }, 8000);

  const frame = (): void => {
    ctx.clearRect(0, 0, w, h);
    for (const s of stars) {
      s.x += s.vx;
      s.y += s.vy;
      if (s.x < 0) s.x += w;
      else if (s.x > w) s.x -= w;
      if (s.y < 0) s.y += h;
      else if (s.y > h) s.y -= h;

      let r = s.r;
      let opacity = s.baseOpacity;
      if (s.flare > 0) {
        opacity = s.baseOpacity + (1 - s.baseOpacity) * s.flare;
        r = s.r * (1 + s.flare); // up to double size at peak
        s.flare = Math.max(0, s.flare - 1 / 60); // fade over ~1s
      }

      ctx.beginPath();
      ctx.arc(s.x, s.y, r, 0, Math.PI * 2);
      ctx.fillStyle = `rgba(255, 255, 255, ${opacity})`;
      ctx.fill();
    }
    requestAnimationFrame(frame);
  };
  requestAnimationFrame(frame);
}
