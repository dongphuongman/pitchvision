import type { ShotType, TeamInfo, TileFrame } from '@/api/types';
import { OBJ, OBJ_CLS } from './tileCodec';

/* OverlayRenderer (AD §11.1): canvas over the video, pixel coords × displayWidth / source width. */

export const REFEREE_COLOR = '#f5d90a';
export const UNKNOWN_COLOR = '#9aa4b2';
export const BALL_COLOR = '#ffffff';

export interface ContentRect {
  /** Pixels on screen per source pixel. */
  scale: number;
  offsetX: number;
  offsetY: number;
  width: number;
  height: number;
}

/**
 * Where a `object-fit: contain` video draws inside its element box. Overlay pixels are in the
 * analysed video's coordinates (`manifest.width × height`, 1280×720 in M1).
 */
export function fitContain(boxW: number, boxH: number, srcW: number, srcH: number): ContentRect {
  if (boxW <= 0 || boxH <= 0 || srcW <= 0 || srcH <= 0) {
    return { scale: 0, offsetX: 0, offsetY: 0, width: 0, height: 0 };
  }
  const scale = Math.min(boxW / srcW, boxH / srcH);
  const width = srcW * scale;
  const height = srcH * scale;
  return { scale, offsetX: (boxW - width) / 2, offsetY: (boxH - height) / 2, width, height };
}

export interface OverlayLayers {
  boxes: boolean;
  labels: boolean;
  conf: boolean;
  ball: boolean;
}

export const DEFAULT_LAYERS: OverlayLayers = { boxes: true, labels: true, conf: true, ball: true };

export type OverlayCommand =
  | { kind: 'box'; x: number; y: number; w: number; h: number; color: string; dashed: boolean }
  | { kind: 'label'; x: number; y: number; text: string; color: string }
  | { kind: 'ball'; x: number; y: number; r: number };

export interface OverlayOptions {
  rect: ContentRect;
  /** team cluster → colour (manifest.teams). */
  teamColors: ReadonlyMap<number, string>;
  layers?: OverlayLayers;
  /** track_id → user-assigned name (SCR-21 Assign). */
  playerNames?: ReadonlyMap<number, string>;
}

export function teamColorMap(teams: readonly TeamInfo[]): Map<number, string> {
  return new Map(teams.map((t) => [t.cluster, t.color]));
}

export function objectColor(
  cls: number,
  team: number,
  teamColors: ReadonlyMap<number, string>,
): string {
  if (cls === OBJ_CLS.referee) return REFEREE_COLOR;
  if (cls === OBJ_CLS.ball) return BALL_COLOR;
  return teamColors.get(team) ?? UNKNOWN_COLOR;
}

/**
 * Pure: one frame → draw commands in screen pixels. Nothing outside `main_wide` shots (replays /
 * close-ups carry no tracking) or when the frame isn't known yet.
 */
export function computeOverlay(
  frame: TileFrame | undefined,
  shot: ShotType | undefined,
  opts: OverlayOptions,
): OverlayCommand[] {
  if (shot !== 'main_wide' || !frame?.objs || opts.rect.scale <= 0) return [];
  const { scale, offsetX, offsetY } = opts.rect;
  const layers = opts.layers ?? DEFAULT_LAYERS;
  const out: OverlayCommand[] = [];
  const labels: OverlayCommand[] = [];
  for (const o of frame.objs) {
    const cls = o[OBJ.cls];
    const x = offsetX + o[OBJ.x1] * scale;
    const y = offsetY + o[OBJ.y1] * scale;
    const w = (o[OBJ.x2] - o[OBJ.x1]) * scale;
    const h = (o[OBJ.y2] - o[OBJ.y1]) * scale;
    if (cls === OBJ_CLS.ball) {
      if (layers.ball) {
        out.push({
          kind: 'ball',
          x: x + w / 2,
          y: y + h / 2,
          r: Math.max(4, Math.min(w, h) / 2 + 2),
        });
      }
      continue;
    }
    const color = objectColor(cls, o[OBJ.team], opts.teamColors);
    if (layers.boxes) {
      out.push({ kind: 'box', x, y, w, h, color, dashed: cls === OBJ_CLS.referee });
    }
    if (layers.labels) {
      const trackId = o[OBJ.trackId];
      const name = opts.playerNames?.get(trackId);
      const id = `${cls === OBJ_CLS.goalkeeper ? 'GK ' : ''}#${trackId}`;
      const text = name
        ? `${name} (${id}${layers.conf ? ` ${o[OBJ.conf].toFixed(2)}` : ''})`
        : layers.conf
          ? `${id} ${o[OBJ.conf].toFixed(2)}`
          : id;
      labels.push({ kind: 'label', x, y: y - 3, text, color });
    }
  }
  // Labels last so boxes never cover them.
  return out.concat(labels);
}

export function paintOverlay(
  ctx: CanvasRenderingContext2D,
  cmds: readonly OverlayCommand[],
  dpr = 1,
): void {
  ctx.save();
  ctx.setTransform(dpr, 0, 0, dpr, 0, 0);
  ctx.clearRect(0, 0, ctx.canvas.width / dpr, ctx.canvas.height / dpr);
  ctx.font = '600 11px Inter Variable, system-ui, sans-serif';
  ctx.textBaseline = 'bottom';
  for (const c of cmds) {
    if (c.kind === 'box') {
      ctx.strokeStyle = c.color;
      ctx.lineWidth = 2;
      ctx.setLineDash(c.dashed ? [5, 3] : []);
      ctx.strokeRect(c.x, c.y, c.w, c.h);
    } else if (c.kind === 'ball') {
      ctx.setLineDash([]);
      ctx.beginPath();
      ctx.arc(c.x, c.y, c.r, 0, Math.PI * 2);
      ctx.fillStyle = BALL_COLOR;
      ctx.fill();
      ctx.lineWidth = 2;
      ctx.strokeStyle = '#0b0f14';
      ctx.stroke();
    } else {
      const w = ctx.measureText(c.text).width + 8;
      ctx.fillStyle = 'rgba(5,9,13,.72)';
      ctx.fillRect(c.x - 1, c.y - 15, w, 15);
      ctx.fillStyle = c.color === UNKNOWN_COLOR ? '#fff' : c.color;
      ctx.fillText(c.text, c.x + 3, c.y - 2);
    }
  }
  ctx.restore();
}

/** Keeps a canvas exactly over the video's displayed content and paints frames into it. */
export class OverlayRenderer {
  private readonly canvas: HTMLCanvasElement;
  private readonly video: HTMLVideoElement;
  private readonly ro: ResizeObserver;
  private rect: ContentRect = { scale: 0, offsetX: 0, offsetY: 0, width: 0, height: 0 };
  private dpr = 1;
  private last: {
    frame: TileFrame | undefined;
    shot: ShotType | undefined;
    playerNames?: ReadonlyMap<number, string>;
  } | null = null;
  layers: OverlayLayers = DEFAULT_LAYERS;

  constructor(
    canvas: HTMLCanvasElement,
    video: HTMLVideoElement,
    private readonly source: {
      width: number;
      height: number;
      teamColors: ReadonlyMap<number, string>;
    },
  ) {
    this.canvas = canvas;
    this.video = video;
    this.ro = new ResizeObserver(() => this.resize());
    this.ro.observe(video);
    video.addEventListener('loadedmetadata', this.resize);
    this.resize();
  }

  readonly resize = (): void => {
    const w = this.video.clientWidth;
    const h = this.video.clientHeight;
    this.dpr = globalThis.devicePixelRatio || 1;
    this.canvas.width = Math.round(w * this.dpr);
    this.canvas.height = Math.round(h * this.dpr);
    this.canvas.style.width = `${w}px`;
    this.canvas.style.height = `${h}px`;
    // The element letterboxes the stream by its intrinsic aspect; tile pixels are in source units.
    const vw = this.video.videoWidth || this.source.width;
    const vh = this.video.videoHeight || this.source.height;
    const content = fitContain(w, h, vw, vh);
    this.rect = { ...content, scale: content.width / this.source.width };
    if (this.last) this.render(this.last.frame, this.last.shot);
  };

  render(
    frame: TileFrame | undefined,
    shot: ShotType | undefined,
    playerNames?: ReadonlyMap<number, string>,
  ): OverlayCommand[] {
    this.last = { frame, shot, playerNames };
    const cmds = computeOverlay(frame, shot, {
      rect: this.rect,
      teamColors: this.source.teamColors,
      layers: this.layers,
      playerNames,
    });
    const ctx = this.canvas.getContext('2d');
    if (ctx) paintOverlay(ctx, cmds, this.dpr);
    return cmds;
  }

  dispose(): void {
    this.ro.disconnect();
    this.video.removeEventListener('loadedmetadata', this.resize);
  }
}
