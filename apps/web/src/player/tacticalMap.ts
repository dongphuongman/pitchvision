import type { ShotType, TileFrame } from '@/api/types';
import { objectColor } from './overlay';
import { OBJ, OBJ_CLS } from './tileCodec';

/* TacticalMapRenderer (AD §11.1): 2D pitch next to the video, metres × scale. */

export const PITCH_LENGTH = 105;
export const PITCH_WIDTH = 68;
/** AD §11.1: below this h_quality the map greys out instead of drawing wrong positions. */
export const HQ_THRESHOLD = 0.5;

export type PitchShape =
  | { kind: 'rect'; x: number; y: number; w: number; h: number }
  | { kind: 'line'; x1: number; y1: number; x2: number; y2: number }
  | { kind: 'circle'; x: number; y: number; r: number }
  | { kind: 'arc'; x: number; y: number; r: number; a0: number; a1: number }
  | { kind: 'spot'; x: number; y: number };

/**
 * FIFA template on a 105×68 m pitch, origin at a corner flag (x along the touchline, y across),
 * the same convention as cv-core's pitch model (centre at 52.5, 34).
 */
export function pitchTemplate(length = PITCH_LENGTH, width = PITCH_WIDTH): PitchShape[] {
  const cy = width / 2;
  const pa = { d: 16.5, w: 40.32 };
  const ga = { d: 5.5, w: 18.32 };
  const spot = 11;
  // Penalty arc: the part of the 9.15 m circle around the spot outside the area.
  const arcA = Math.acos((pa.d - spot) / 9.15);
  return [
    { kind: 'rect', x: 0, y: 0, w: length, h: width },
    { kind: 'line', x1: length / 2, y1: 0, x2: length / 2, y2: width },
    { kind: 'circle', x: length / 2, y: cy, r: 9.15 },
    { kind: 'spot', x: length / 2, y: cy },
    { kind: 'rect', x: 0, y: cy - pa.w / 2, w: pa.d, h: pa.w },
    { kind: 'rect', x: length - pa.d, y: cy - pa.w / 2, w: pa.d, h: pa.w },
    { kind: 'rect', x: 0, y: cy - ga.w / 2, w: ga.d, h: ga.w },
    { kind: 'rect', x: length - ga.d, y: cy - ga.w / 2, w: ga.d, h: ga.w },
    { kind: 'spot', x: spot, y: cy },
    { kind: 'spot', x: length - spot, y: cy },
    { kind: 'arc', x: spot, y: cy, r: 9.15, a0: -arcA, a1: arcA },
    { kind: 'arc', x: length - spot, y: cy, r: 9.15, a0: Math.PI - arcA, a1: Math.PI + arcA },
  ];
}

export type MapState = 'ok' | 'low_confidence' | 'not_available' | 'no_data';

export interface MapDot {
  x: number;
  y: number;
  color: string;
  kind: 'player' | 'goalkeeper' | 'referee' | 'ball';
  trackId: number;
  name?: string;
}

export interface MapScene {
  state: MapState;
  /** The shot when `not_available` (label: "Not available — replay"). */
  shot?: ShotType;
  /** Metres; empty unless `ok`. */
  dots: MapDot[];
  hq: number | null;
}

const KIND: Record<number, MapDot['kind']> = {
  [OBJ_CLS.player]: 'player',
  [OBJ_CLS.goalkeeper]: 'goalkeeper',
  [OBJ_CLS.referee]: 'referee',
  [OBJ_CLS.ball]: 'ball',
};

/** Pure: frame → what the map shows. */
export function computeMapScene(
  frame: TileFrame | undefined,
  shot: ShotType | undefined,
  opts: {
    teamColors: ReadonlyMap<number, string>;
    hqThreshold?: number;
    playerNames?: ReadonlyMap<number, string>;
  },
): MapScene {
  if (shot === undefined) return { state: 'no_data', dots: [], hq: null };
  if (shot !== 'main_wide') return { state: 'not_available', shot, dots: [], hq: null };
  if (!frame) return { state: 'no_data', dots: [], hq: null };
  const hq = frame.hq ?? null;
  if (hq === null || hq < (opts.hqThreshold ?? HQ_THRESHOLD)) {
    return { state: 'low_confidence', dots: [], hq };
  }
  const dots: MapDot[] = [];
  for (const o of frame.objs ?? []) {
    const px = o[OBJ.px];
    const py = o[OBJ.py];
    if (px === null || py === null) continue;
    const cls = o[OBJ.cls];
    const trackId = o[OBJ.trackId];
    dots.push({
      x: px,
      y: py,
      color: objectColor(cls, o[OBJ.team], opts.teamColors),
      kind: KIND[cls] ?? 'player',
      trackId,
      name: opts.playerNames?.get(trackId),
    });
  }
  return { state: 'ok', dots, hq };
}

export interface MapTransform {
  scale: number;
  ox: number;
  oy: number;
}

/** Fit the pitch (+ margin in metres) into a canvas, centred. */
export function mapTransform(w: number, h: number, marginM = 3): MapTransform {
  const L = PITCH_LENGTH + marginM * 2;
  const W = PITCH_WIDTH + marginM * 2;
  const scale = Math.max(0, Math.min(w / L, h / W));
  return {
    scale,
    ox: (w - PITCH_LENGTH * scale) / 2,
    oy: (h - PITCH_WIDTH * scale) / 2,
  };
}

export interface MapLabels {
  lowConfidence: string;
  notAvailable: (shot: ShotType) => string;
}

/** The FIFA markings through `t` (shared by the tactical map and the SCR-20c heatmap). */
export function strokePitch(
  ctx: CanvasRenderingContext2D,
  t: { ox: number; oy: number; scale: number },
  color: string,
): void {
  ctx.strokeStyle = color;
  ctx.fillStyle = color;
  ctx.lineWidth = 1.25;
  const X = (m: number) => t.ox + m * t.scale;
  const Y = (m: number) => t.oy + m * t.scale;
  for (const s of pitchTemplate()) {
    ctx.beginPath();
    if (s.kind === 'rect') ctx.rect(X(s.x), Y(s.y), s.w * t.scale, s.h * t.scale);
    else if (s.kind === 'line') {
      ctx.moveTo(X(s.x1), Y(s.y1));
      ctx.lineTo(X(s.x2), Y(s.y2));
    } else if (s.kind === 'circle') ctx.arc(X(s.x), Y(s.y), s.r * t.scale, 0, Math.PI * 2);
    else if (s.kind === 'arc') ctx.arc(X(s.x), Y(s.y), s.r * t.scale, s.a0, s.a1);
    else {
      ctx.arc(X(s.x), Y(s.y), 1.5, 0, Math.PI * 2);
      ctx.fill();
      continue;
    }
    ctx.stroke();
  }
}

export function paintMap(
  ctx: CanvasRenderingContext2D,
  scene: MapScene,
  labels: MapLabels,
  dpr = 1,
): void {
  const w = ctx.canvas.width / dpr;
  const h = ctx.canvas.height / dpr;
  const t = mapTransform(w, h);
  const dim = scene.state !== 'ok';
  ctx.save();
  ctx.setTransform(dpr, 0, 0, dpr, 0, 0);
  ctx.clearRect(0, 0, w, h);
  ctx.fillStyle = dim ? '#2a3138' : '#1d4f28';
  ctx.fillRect(0, 0, w, h);
  strokePitch(ctx, t, dim ? 'rgba(255,255,255,.22)' : 'rgba(255,255,255,.7)');
  const X = (m: number) => t.ox + m * t.scale;
  const Y = (m: number) => t.oy + m * t.scale;
  for (const d of scene.dots) {
    ctx.beginPath();
    const r = d.kind === 'ball' ? 3 : 5.5;
    ctx.arc(X(d.x), Y(d.y), r, 0, Math.PI * 2);
    ctx.fillStyle = d.color;
    ctx.fill();
    ctx.lineWidth = 1.5;
    ctx.strokeStyle = d.kind === 'ball' ? '#0b0f14' : 'rgba(255,255,255,.9)';
    ctx.stroke();
    if (d.name && d.kind !== 'ball') {
      ctx.font = '500 11px Inter Variable, system-ui, sans-serif';
      ctx.textAlign = 'center';
      ctx.textBaseline = 'bottom';
      const nameX = X(d.x);
      const nameY = Y(d.y) - r - 4;
      const tw = ctx.measureText(d.name).width + 8;
      ctx.fillStyle = 'rgba(5,9,13,.72)';
      ctx.fillRect(nameX - tw / 2, nameY - 14, tw, 15);
      ctx.fillStyle = '#fff';
      ctx.fillText(d.name, nameX, nameY);
    }
  }
  const label =
    scene.state === 'low_confidence'
      ? labels.lowConfidence
      : scene.state === 'not_available' && scene.shot
        ? labels.notAvailable(scene.shot)
        : null;
  if (label) {
    ctx.font = '600 13px Inter Variable, system-ui, sans-serif';
    ctx.textAlign = 'center';
    ctx.textBaseline = 'middle';
    const tw = ctx.measureText(label).width + 20;
    ctx.fillStyle = 'rgba(5,9,13,.78)';
    ctx.fillRect(w / 2 - tw / 2, h / 2 - 14, tw, 28);
    ctx.fillStyle = '#e6edf3';
    ctx.fillText(label, w / 2, h / 2);
  }
  ctx.restore();
}

/** Canvas tactical map that tracks its element size and repaints the last scene on resize. */
export class TacticalMapRenderer {
  private readonly ro: ResizeObserver;
  private dpr = 1;
  private last: {
    frame: TileFrame | undefined;
    shot: ShotType | undefined;
    playerNames: ReadonlyMap<number, string> | undefined;
  } | null = null;

  constructor(
    private readonly canvas: HTMLCanvasElement,
    private readonly opts: {
      teamColors: ReadonlyMap<number, string>;
      labels: MapLabels;
      hqThreshold?: number;
    },
  ) {
    this.ro = new ResizeObserver(() => this.resize());
    this.ro.observe(canvas);
    this.resize();
  }

  private resize(): void {
    this.dpr = globalThis.devicePixelRatio || 1;
    this.canvas.width = Math.round(this.canvas.clientWidth * this.dpr);
    this.canvas.height = Math.round(this.canvas.clientHeight * this.dpr);
    if (this.last) this.render(this.last.frame, this.last.shot, this.last.playerNames);
  }

  render(
    frame: TileFrame | undefined,
    shot: ShotType | undefined,
    playerNames?: ReadonlyMap<number, string>,
  ): MapScene {
    this.last = { frame, shot, playerNames };
    const scene = computeMapScene(frame, shot, { ...this.opts, playerNames });
    const ctx = this.canvas.getContext('2d');
    if (ctx) paintMap(ctx, scene, this.opts.labels, this.dpr);
    return scene;
  }

  dispose(): void {
    this.ro.disconnect();
  }
}
