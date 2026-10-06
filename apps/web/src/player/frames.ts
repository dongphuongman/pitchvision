/**
 * Frame ↔ time math on the normalized timeline (target fps, 25 in M1).
 *
 * SP-08 (docs/status.json): requestVideoFrameCallback's `metadata.mediaTime` is the presented
 * frame's PTS, so `round(mediaTime × fps)` is frame-exact (100 % at 1×, seeks 40/40). Reading
 * `currentTime` from rAF is 1–4 frames off; it is only the fallback for browsers without rVFC.
 */

export const DEFAULT_FPS = 25;
export const DEFAULT_TILE_MS = 10_000;

/** rVFC: presented frame PTS → frame index (nearest; absorbs float jitter either side). */
export function frameFromMediaTime(mediaTime: number, fps: number): number {
  return Math.max(0, Math.round(mediaTime * fps));
}

/** Fallback: a playback position lies inside a frame interval → floor (approximate while playing). */
export function frameFromCurrentTime(currentTime: number, fps: number): number {
  return Math.max(0, Math.floor(currentTime * fps + 1e-6));
}

/** Seek target for frame `f`: the middle of its interval, so the decoder must show `f` (SP-08). */
export function timeForFrame(f: number, fps: number): number {
  return (Math.max(0, f) + 0.5) / fps;
}

export function msForFrame(f: number, fps: number): number {
  return (f * 1000) / fps;
}

export function frameForMs(ms: number, fps: number): number {
  return Math.max(0, Math.floor((ms * fps) / 1000 + 1e-6));
}

export function framesPerTile(tileMs: number, fps: number): number {
  return Math.round((tileMs * fps) / 1000);
}

export function tileForFrame(f: number, fpt: number): number {
  return Math.floor(Math.max(0, f) / fpt);
}

/** `{tile_no}` in the manifest template is zero-padded to 6 digits. */
export function tileUrl(template: string, tileNo: number): string {
  return template.replace('{tile_no}', String(tileNo).padStart(6, '0'));
}
