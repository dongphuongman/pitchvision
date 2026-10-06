import { ChevronLeft, ChevronRight, FileVideo, Pause, Play } from 'lucide-react';
import { useEffect, useMemo, useState, type ChangeEvent } from 'react';
import { useTranslation } from 'react-i18next';
import type { Manifest, ShotType } from '@/api/types';
import { mockManifest, mockTileFetch } from '@/mocks/player';
import { msForFrame } from '@/player/frames';
import { ShotTimeline } from '@/player/ShotTimeline';
import type { MapLabels } from '@/player/tacticalMap';
import { useClockState, useSyncEngine } from '@/player/useSyncEngine';
import { Badge, IconButton, Select, Tooltip } from '@/ui';

const SPEEDS = ['0.25', '0.5', '1', '1.5', '2'];
// With `VITE_MOCK_API=1` the MSW worker serves the tiles over HTTP (the real fetch path);
// otherwise the generator answers in-page.
const tileFetch = import.meta.env.VITE_MOCK_API === '1' ? undefined : mockTileFetch;

/**
 * Dev-only sync-engine bench (/__player): pick any local video; a synthetic manifest + tiles
 * (src/mocks/player.ts) are generated for its duration so overlay, tactical map, shot timeline,
 * prefetch and seek behaviour can be eyeballed. Not part of production builds.
 */
export default function PlayerDevPage() {
  const { t } = useTranslation('video');
  const { t: tc } = useTranslation();
  const [src, setSrc] = useState<string | null>(null);
  const [name, setName] = useState('');
  const [video, setVideo] = useState<HTMLVideoElement | null>(null);
  const [overlay, setOverlay] = useState<HTMLCanvasElement | null>(null);
  const [map, setMap] = useState<HTMLCanvasElement | null>(null);
  const [manifest, setManifest] = useState<Manifest | null>(null);

  useEffect(
    () => () => {
      if (src) URL.revokeObjectURL(src);
    },
    [src],
  );

  const mapLabels = useMemo<MapLabels>(
    () => ({
      lowConfidence: t('player.mapLowConfidence'),
      notAvailable: (shot: ShotType) =>
        t('player.mapNotAvailable', { shot: tc(`status.shot.${shot}`).toLowerCase() }),
    }),
    [t, tc],
  );

  const { clock, store } = useSyncEngine({
    manifest,
    video,
    overlayCanvas: overlay,
    mapCanvas: map,
    mapLabels,
    fetch: tileFetch,
  });
  const state = useClockState(clock);

  const pick = (e: ChangeEvent<HTMLInputElement>) => {
    const file = e.target.files?.[0];
    if (!file) return;
    setManifest(null);
    setName(file.name);
    setSrc(URL.createObjectURL(file));
  };

  const onMeta = () => {
    if (!video || !Number.isFinite(video.duration)) return;
    setManifest(
      mockManifest({ videoId: 'dev-player', durationMs: Math.round(video.duration * 1000) }),
    );
  };

  const frame = state?.frameIdx ?? 0;
  const currentMs = manifest ? msForFrame(frame, manifest.fps) : 0;

  return (
    <div className="mx-auto max-w-[1500px] px-5 py-5">
      <div className="mb-4 flex flex-wrap items-center gap-3">
        <h1 className="m-0 text-xl">Sync engine bench</h1>
        <Badge tone="mute">dev</Badge>
        <div className="flex-1" />
        <label className="inline-flex cursor-pointer items-center gap-2 rounded-md border border-line-strong bg-surface-2 px-3.5 py-1.5 text-control hover:bg-surface-3">
          <FileVideo size={16} aria-hidden="true" />
          {name || 'Choose a local video…'}
          <input
            type="file"
            accept="video/*"
            className="sr-only"
            onChange={pick}
            data-testid="video-file"
          />
        </label>
      </div>

      <div className="grid gap-4 lg:grid-cols-[minmax(0,1fr)_420px]">
        <div className="flex min-w-0 flex-col gap-3">
          <div className="relative aspect-video overflow-hidden rounded-xl bg-black">
            {src ? (
              <video
                ref={setVideo}
                src={src}
                className="absolute inset-0 size-full object-contain"
                onLoadedMetadata={onMeta}
                playsInline
                muted
              />
            ) : (
              <div className="grid size-full place-items-center text-fg-3">
                Pick a video file to start (any length; overlays are synthetic).
              </div>
            )}
            <canvas
              ref={setOverlay}
              aria-hidden="true"
              className="pointer-events-none absolute top-0 left-0"
            />
          </div>

          <div className="flex flex-wrap items-center gap-2">
            <IconButton
              label={state?.playing ? t('player.pause') : t('player.play')}
              icon={state?.playing ? <Pause size={17} /> : <Play size={17} />}
              disabled={!clock}
              onClick={() => clock?.togglePlay()}
            />
            <IconButton
              label={t('player.prevFrame')}
              icon={<ChevronLeft size={17} />}
              disabled={!clock}
              onClick={() => clock?.step(-1)}
            />
            <IconButton
              label={t('player.nextFrame')}
              icon={<ChevronRight size={17} />}
              disabled={!clock}
              onClick={() => clock?.step(1)}
            />
            <Select
              size="sm"
              aria-label={t('player.speed')}
              className="w-[86px]"
              value={String(state?.rate ?? 1)}
              onValueChange={(v) => clock?.setRate(Number(v))}
              options={SPEEDS.map((s) => ({ value: s, label: `${s}×` }))}
            />
            <span
              className="font-mono text-caption text-fg-2 tabular-nums"
              data-testid="frame-info"
            >
              {t('player.frameInfo', {
                fps: manifest?.fps ?? 25,
                frame,
                total: manifest?.frame_count ?? 0,
              })}
            </span>
            {state?.approximate && (
              <Tooltip content={t('player.approximateHint')}>
                <Badge tone="warn" data-testid="approximate">
                  {t('player.approximate')}
                </Badge>
              </Tooltip>
            )}
            <div className="flex-1" />
            {store && (
              <span className="font-mono text-[11.5px] text-fg-3" data-testid="tile-stats">
                tile {store.tileForFrame(frame)} · cached [{store.cachedTiles().join(',')}] ·
                loading [{store.inflightTiles().join(',')}]
              </span>
            )}
          </div>

          {manifest && (
            <ShotTimeline
              shots={manifest.shots}
              durationMs={manifest.duration_ms}
              currentMs={currentMs}
              onSeek={(ms) => clock?.seekToMs(ms)}
            />
          )}
        </div>

        <div className="flex flex-col gap-2">
          <h2 className="m-0 text-control font-semibold text-fg-2">{t('player.map')}</h2>
          <canvas
            ref={setMap}
            aria-label={t('player.map')}
            className="aspect-[111/74] w-full rounded-xl bg-surface-2"
          />
          {manifest && (
            <div className="flex flex-wrap gap-3 text-caption text-fg-3">
              {manifest.teams.map((team) => (
                <span key={team.cluster} className="inline-flex items-center gap-1.5">
                  <span className="size-2.5 rounded-full" style={{ background: team.color }} />
                  {team.name}
                </span>
              ))}
              <span>
                wide {manifest.coverage.main_wide_pct}% · {manifest.tile_count} tiles
              </span>
            </div>
          )}
        </div>
      </div>
    </div>
  );
}
