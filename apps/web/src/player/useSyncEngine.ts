import { useEffect, useMemo, useRef, useSyncExternalStore } from 'react';
import type { Manifest } from '@/api/types';
import { msForFrame } from './frames';
import { makeOverrider } from './overrides';
import { DEFAULT_LAYERS, OverlayRenderer, teamColorMap, type OverlayLayers } from './overlay';
import { PlaybackClock, type ClockState } from './PlaybackClock';
import { shotAt } from './shots';
import { TacticalMapRenderer, type MapLabels } from './tacticalMap';
import { createTileDecoder, type TileDecoder } from './TileDecoder';
import { TileStore } from './TileStore';

/** PlaybackClock for a `<video>` (null until mounted); running while mounted. */
export function usePlaybackClock(video: HTMLVideoElement | null, fps: number) {
  const clock = useMemo(() => (video ? new PlaybackClock(video, { fps }) : null), [video, fps]);
  useEffect(() => {
    if (!clock) return;
    clock.start();
    return () => clock.stop();
  }, [clock]);
  return clock;
}

const noop = () => () => undefined;

/** Re-renders on every clock change (frame, play/pause, rate). Keep consumers small. */
export function useClockState(clock: PlaybackClock | null): ClockState | null {
  return useSyncExternalStore(
    clock ? (cb) => clock.subscribe(cb) : noop,
    () => clock?.getState() ?? null,
    () => null,
  );
}

/** TileStore for a manifest; loads are aborted and the decode Worker released on unmount. */
export function useTileStore(
  manifest: Manifest | null,
  opts: { fetch?: typeof fetch; decoder?: () => TileDecoder } = {},
) {
  const { fetch: fetchImpl, decoder } = opts;
  const store = useMemo(
    () =>
      manifest
        ? new TileStore({
            urlTemplate: manifest.tile_url_template,
            tileCount: manifest.tile_count,
            fps: manifest.fps,
            tileMs: manifest.tile_ms,
            decoder: decoder ?? createTileDecoder,
            fetch: fetchImpl,
          })
        : null,
    [manifest, fetchImpl, decoder],
  );
  useEffect(() => {
    if (!store) return;
    return () => store.close();
  }, [store]);
  return store;
}

export interface SyncEngineOptions {
  manifest: Manifest | null;
  video: HTMLVideoElement | null;
  overlayCanvas: HTMLCanvasElement | null;
  mapCanvas: HTMLCanvasElement | null;
  mapLabels: MapLabels;
  /** E13 layer toggles; changing them repaints without rebuilding the engine. */
  layers?: OverlayLayers;
  fetch?: typeof fetch;
  decoder?: () => TileDecoder;
}

/**
 * Wires AD §11.1: `<video>` → PlaybackClock → TileStore → OverlayRenderer + TacticalMapRenderer.
 * Drawing happens in the clock / tile callbacks (no React render per frame); a seek re-targets
 * the TileStore before the frame arrives so stale prefetches are cancelled.
 */
export function useSyncEngine(opts: SyncEngineOptions) {
  const { manifest, video, overlayCanvas, mapCanvas, mapLabels } = opts;
  const clock = usePlaybackClock(video, manifest?.fps ?? 25);
  const store = useTileStore(manifest, { fetch: opts.fetch, decoder: opts.decoder });
  const overlayRef = useRef<OverlayRenderer | null>(null);
  const redrawRef = useRef<(() => void) | null>(null);
  const layers = opts.layers ?? DEFAULT_LAYERS;
  const layersRef = useRef(layers);

  useEffect(() => {
    if (!clock || !store || !manifest || !video) return;
    const teamColors = teamColorMap(manifest.teams);
    const fix = makeOverrider(manifest.track_overrides);
    const overlay = overlayCanvas
      ? new OverlayRenderer(overlayCanvas, video, {
          width: manifest.width,
          height: manifest.height,
          teamColors,
        })
      : null;
    if (overlay) overlay.layers = layersRef.current; // a rebuilt engine keeps the user's toggles
    const map = mapCanvas
      ? new TacticalMapRenderer(mapCanvas, { teamColors, labels: mapLabels })
      : null;

    const draw = () => {
      const { frameIdx } = clock.getState();
      store.setPlayhead(frameIdx);
      const lookup = store.frame(frameIdx);
      // Before the tile arrives the manifest still knows the shot (map can say "replay").
      const shot = lookup.shot ?? shotAt(manifest.shots, msForFrame(frameIdx, manifest.fps))?.type;
      const frame = fix(lookup.frame);
      overlay?.render(frame, lookup.loaded ? shot : undefined);
      map?.render(frame, shot === 'main_wide' && !lookup.loaded ? undefined : shot);
    };
    overlayRef.current = overlay;
    redrawRef.current = draw;
    const offClock = clock.subscribe(draw);
    const offSeek = clock.onSeek((f) => store.seek(f));
    const offTile = store.onTile((tileNo) => {
      if (tileNo === store.tileForFrame(clock.getState().frameIdx)) draw();
    });
    draw();
    return () => {
      offClock();
      offSeek();
      offTile();
      overlay?.dispose();
      map?.dispose();
      overlayRef.current = null;
      redrawRef.current = null;
    };
  }, [clock, store, manifest, video, overlayCanvas, mapCanvas, mapLabels]);

  // Layers live outside the effect above so toggling them doesn't tear down renderers / tiles.
  useEffect(() => {
    layersRef.current = layers;
    if (overlayRef.current) overlayRef.current.layers = layers;
    redrawRef.current?.();
  }, [layers]);

  return { clock, store };
}
