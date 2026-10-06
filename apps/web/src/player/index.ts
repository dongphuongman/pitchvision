/**
 * Sync engine (AD §11.1). Framework-agnostic core + thin React hooks:
 *   <video> → PlaybackClock (exact frame_idx via rVFC) → TileStore (LRU 30, prefetch ±2,
 *   Web Worker decode) → OverlayRenderer (canvas on the video) / TacticalMapRenderer (pitch).
 */
export * from './frames';
export { PlaybackClock } from './PlaybackClock';
export type { ClockState, FrameScheduler, PlaybackClockOptions, VideoLike } from './PlaybackClock';
export { TileStore } from './TileStore';
export type { DecodedTile, FrameLookup, TileStoreOptions } from './TileStore';
export { createTileDecoder, InlineTileDecoder, WorkerTileDecoder } from './TileDecoder';
export type { TileDecoder } from './TileDecoder';
export { decodeTile, OBJ, OBJ_CLS, parseTile, TileFormatError } from './tileCodec';
export {
  computeOverlay,
  fitContain,
  OverlayRenderer,
  teamColorMap,
  type OverlayCommand,
  type OverlayLayers,
} from './overlay';
export {
  computeMapScene,
  HQ_THRESHOLD,
  mapTransform,
  pitchTemplate,
  TacticalMapRenderer,
  type MapLabels,
  type MapScene,
} from './tacticalMap';
export { nextLiveActionMs, shotAt, timelineSegments } from './shots';
export { ShotTimeline } from './ShotTimeline';
export { useClockState, usePlaybackClock, useSyncEngine, useTileStore } from './useSyncEngine';
