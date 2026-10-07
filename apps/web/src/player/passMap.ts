import type { Pass } from '@/api/types';
import { mapTransform, strokePitch } from './tacticalMap';

/* SCR-20d pass map: every inferred pass as an arrow on the pitch (completed = team colour, lost = dashed red). */

export interface PassMapOptions {
  color: string;
  highlight?: number | null; // index into `passes`
}

export function paintPassMap(
  ctx: CanvasRenderingContext2D,
  passes: readonly Pass[],
  opts: PassMapOptions,
  dpr = 1,
): void {
  const w = ctx.canvas.width / dpr;
  const h = ctx.canvas.height / dpr;
  const t = mapTransform(w, h);
  const X = (m: number) => t.ox + m * t.scale;
  const Y = (m: number) => t.oy + m * t.scale;
  ctx.save();
  ctx.setTransform(dpr, 0, 0, dpr, 0, 0);
  ctx.fillStyle = '#1d4f28';
  ctx.fillRect(0, 0, w, h);
  strokePitch(ctx, t, 'rgba(255,255,255,.6)');
  passes.forEach((p, i) => {
    const hl = opts.highlight === i;
    const color = p.completed ? opts.color : '#ff5a4f';
    ctx.globalAlpha = opts.highlight == null || hl ? 1 : 0.35;
    ctx.strokeStyle = color;
    ctx.fillStyle = color;
    ctx.lineWidth = hl ? 3 : 1.8;
    ctx.setLineDash(p.completed ? [] : [5, 4]);
    const x0 = X(p.x0);
    const y0 = Y(p.y0);
    const x1 = X(p.x1);
    const y1 = Y(p.y1);
    ctx.beginPath();
    ctx.moveTo(x0, y0);
    ctx.lineTo(x1, y1);
    ctx.stroke();
    ctx.setLineDash([]);
    const a = Math.atan2(y1 - y0, x1 - x0);
    const s = hl ? 9 : 7;
    ctx.beginPath();
    ctx.moveTo(x1, y1);
    ctx.lineTo(x1 - s * Math.cos(a - 0.45), y1 - s * Math.sin(a - 0.45));
    ctx.lineTo(x1 - s * Math.cos(a + 0.45), y1 - s * Math.sin(a + 0.45));
    ctx.closePath();
    ctx.fill();
    ctx.beginPath();
    ctx.arc(x0, y0, hl ? 4 : 3, 0, Math.PI * 2);
    ctx.fill();
  });
  ctx.restore();
}

export function renderPassMap(
  canvas: HTMLCanvasElement,
  passes: readonly Pass[],
  opts: PassMapOptions,
): void {
  const dpr = window.devicePixelRatio || 1;
  const { width, height } = canvas.getBoundingClientRect();
  canvas.width = Math.round(width * dpr);
  canvas.height = Math.round(height * dpr);
  const ctx = canvas.getContext('2d');
  if (ctx) paintPassMap(ctx, passes, opts, dpr);
}
