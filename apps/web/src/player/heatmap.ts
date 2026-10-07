import type { Heatmap } from '@/api/types';
import { mapTransform, strokePitch } from './tacticalMap';

/* SCR-20c heatmap (FR-503: 2D grid with Gaussian smoothing) + average positions (FR-504: centroid + spread). */

/** Separable Gaussian blur of a row-major grid (σ in cells), edges clamped. */
export function smoothGrid(
  values: ArrayLike<number>,
  cols: number,
  rows: number,
  sigma: number,
): Float32Array {
  const src = Float32Array.from(values);
  if (sigma <= 0) return src;
  const r = Math.max(1, Math.round(sigma * 3));
  const k = Array.from({ length: 2 * r + 1 }, (_, i) => Math.exp(-0.5 * ((i - r) / sigma) ** 2));
  const ks = k.reduce((a, b) => a + b, 0);
  const tmp = new Float32Array(src.length);
  const out = new Float32Array(src.length);
  for (let y = 0; y < rows; y++)
    for (let x = 0; x < cols; x++) {
      let acc = 0;
      for (let i = -r; i <= r; i++)
        acc += (k[i + r] ?? 0) * (src[y * cols + Math.min(cols - 1, Math.max(0, x + i))] ?? 0);
      tmp[y * cols + x] = acc / ks;
    }
  for (let y = 0; y < rows; y++)
    for (let x = 0; x < cols; x++) {
      let acc = 0;
      for (let i = -r; i <= r; i++)
        acc += (k[i + r] ?? 0) * (tmp[Math.min(rows - 1, Math.max(0, y + i)) * cols + x] ?? 0);
      out[y * cols + x] = acc / ks;
    }
  return out;
}

const STOPS = [
  { at: 0, rgb: [40, 120, 220] },
  { at: 0.45, rgb: [250, 220, 60] },
  { at: 1, rgb: [235, 60, 40] },
] as const;

/** Cold → hot ramp over the dark pitch: transparent green-blue → yellow → red. `v` in 0..1. */
export function heatColor(v: number): [number, number, number, number] {
  const t = Math.max(0, Math.min(1, v));
  const [lo, hi] = t <= 0.45 ? [STOPS[0], STOPS[1]] : [STOPS[1], STOPS[2]];
  const f = (t - lo.at) / (hi.at - lo.at);
  const c: [number, number, number] = [0, 1, 2].map((j) =>
    Math.round((lo.rgb[j] ?? 0) + ((hi.rgb[j] ?? 0) - (lo.rgb[j] ?? 0)) * f),
  ) as [number, number, number];
  return [...c, Math.round(255 * Math.min(1, t * 1.6) * 0.85)];
}

export interface AveragePosition {
  x: number;
  y: number;
  /** [σxx², σyy², σxy] m² */
  cov: number[] | null;
  color: string;
  label: string;
}

/** 1-σ ellipse of a 2×2 covariance: semi-axes (m) and rotation (rad). */
export function spreadEllipse(cov: number[]): { rx: number; ry: number; angle: number } {
  const [a = 0, d = 0, b = 0] = cov;
  const tr = (a + d) / 2;
  const disc = Math.sqrt(Math.max(0, ((a - d) / 2) ** 2 + b * b));
  const l1 = Math.max(0, tr + disc);
  const l2 = Math.max(0, tr - disc);
  return { rx: Math.sqrt(l1), ry: Math.sqrt(l2), angle: 0.5 * Math.atan2(2 * b, a - d) };
}

/** Pitch + smoothed heat (+ average positions). Draws in CSS pixels; `dpr` scales the backing store. */
export function paintHeatmap(
  ctx: CanvasRenderingContext2D,
  heat: Heatmap | null,
  avg: AveragePosition[],
  dpr = 1,
): void {
  const w = ctx.canvas.width / dpr;
  const h = ctx.canvas.height / dpr;
  const t = mapTransform(w, h);
  ctx.save();
  ctx.setTransform(dpr, 0, 0, dpr, 0, 0);
  ctx.clearRect(0, 0, w, h);
  ctx.fillStyle = '#1d4f28';
  ctx.fillRect(0, 0, w, h);
  if (heat && heat.total > 0) {
    const g = smoothGrid(heat.counts, heat.cols, heat.rows, 1.2);
    let max = 0;
    for (const v of g) max = Math.max(max, v);
    const off = document.createElement('canvas');
    off.width = heat.cols;
    off.height = heat.rows;
    const octx = off.getContext('2d');
    if (octx && max > 0) {
      const img = octx.createImageData(heat.cols, heat.rows);
      g.forEach((v, i) => img.data.set(heatColor(v / max), i * 4));
      octx.putImageData(img, 0, 0);
      ctx.imageSmoothingEnabled = true;
      ctx.imageSmoothingQuality = 'high';
      const gw = heat.cols * heat.cell_m * t.scale;
      const gh = heat.rows * heat.cell_m * t.scale;
      ctx.drawImage(off, t.ox, t.oy, gw, gh);
    }
  }
  strokePitch(ctx, t, 'rgba(255,255,255,.75)');
  for (const p of avg) {
    const X = t.ox + p.x * t.scale;
    const Y = t.oy + p.y * t.scale;
    if (p.cov) {
      const e = spreadEllipse(p.cov);
      ctx.beginPath();
      ctx.ellipse(
        X,
        Y,
        Math.max(2, e.rx * t.scale),
        Math.max(2, e.ry * t.scale),
        e.angle,
        0,
        Math.PI * 2,
      );
      ctx.strokeStyle = p.color;
      ctx.globalAlpha = 0.55;
      ctx.lineWidth = 1.5;
      ctx.stroke();
      ctx.globalAlpha = 1;
    }
    ctx.beginPath();
    ctx.arc(X, Y, 5.5, 0, Math.PI * 2);
    ctx.fillStyle = p.color;
    ctx.fill();
    ctx.lineWidth = 1.5;
    ctx.strokeStyle = 'rgba(255,255,255,.9)';
    ctx.stroke();
  }
  ctx.restore();
}

/** Size `canvas` to its box (device pixels) and paint — call on data changes and resizes. */
export function renderHeatmap(
  canvas: HTMLCanvasElement,
  heat: Heatmap | null,
  avg: AveragePosition[],
): void {
  const dpr = window.devicePixelRatio || 1;
  const { width, height } = canvas.getBoundingClientRect();
  canvas.width = Math.round(width * dpr);
  canvas.height = Math.round(height * dpr);
  const ctx = canvas.getContext('2d');
  if (ctx) paintHeatmap(ctx, heat, avg, dpr);
}
