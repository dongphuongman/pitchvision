import { useMutation, useQuery } from '@tanstack/react-query';
import { getRouteApi, Link } from '@tanstack/react-router';
import {
  Download,
  ChevronRight,
  Crosshair,
  Maximize,
  MoreVertical,
  Pause,
  Play,
  Shirt,
  SkipBack,
  SkipForward,
} from 'lucide-react';
import { useEffect, useMemo, useRef, useState } from 'react';
import { useTranslation } from 'react-i18next';
import { postRepitch } from '@/api/corrections';
import { useMeta } from '@/api/queries';
import { manifestQuery, mediaQuery } from '@/api/results';
import type { ShotType } from '@/api/types';
import { videoQuery } from '@/api/videos';
import { can } from '@/auth/roles';
import { ExportDialog } from '@/features/exports/ExportDialog';
import { useRole } from '@/auth/session';
import { useFormat } from '@/i18n/format';
import { cn } from '@/lib/cn';
import { msForFrame } from '@/player/frames';
import { DEFAULT_LAYERS, type OverlayLayers } from '@/player/overlay';
import { ShotTimeline } from '@/player/ShotTimeline';
import { useClockState, useSyncEngine } from '@/player/useSyncEngine';
import {
  Banner,
  Button,
  Card,
  Checkbox,
  DropdownMenu,
  DropdownMenuContent,
  DropdownMenuItem,
  DropdownMenuTrigger,
  IconButton,
  SegmentedControl,
  Select,
  Skeleton,
  Tabs,
  toast,
  TabsContent,
  TabsList,
  TabsTrigger,
} from '@/ui';
import { WORKSPACE_TABS, type WorkspaceTab } from './tabs';

const route = getRouteApi('/_app/videos/$videoId/$tab');
const SPEEDS = ['0.25', '0.5', '1', '1.5', '2'] as const;
const LAYER_KEYS = ['boxes', 'labels', 'conf', 'ball'] as const;

function isTypingTarget(el: EventTarget | null): boolean {
  return (
    el instanceof HTMLElement &&
    (el.isContentEditable || ['INPUT', 'TEXTAREA', 'SELECT'].includes(el.tagName))
  );
}

type Pane = 'video' | 'map' | 'both';
const PANES: readonly Pane[] = ['video', 'map', 'both'];

/** SCR-20 Analysis workspace (M1-FE-12): video + overlay ↔ tactical map, shot timeline, tabs. */
export function WorkspacePage() {
  const { videoId, tab } = route.useParams();
  const { t: startSeconds, export: exportTab } = route.useSearch();
  const navigate = route.useNavigate();
  const { t } = useTranslation('video');
  const { t: tc } = useTranslation();
  const fmt = useFormat();
  const canEdit = can(useRole(), 'editor');
  const [pane, setPane] = useState<Pane>('both');
  const meta = useMeta();
  const repitch = useMutation({
    mutationFn: () => postRepitch(videoId),
    onSuccess: () => void navigate({ to: '/videos/$videoId/processing', params: { videoId } }),
    onError: () => toast({ title: t('workspace.mapPitchFailed'), tone: 'error' }),
  });

  const video = useQuery(videoQuery(videoId));
  const manifest = useQuery(manifestQuery(videoId));
  const media = useQuery(mediaQuery(videoId));

  const [videoEl, setVideoEl] = useState<HTMLVideoElement | null>(null);
  const [overlayEl, setOverlayEl] = useState<HTMLCanvasElement | null>(null);
  const [mapEl, setMapEl] = useState<HTMLCanvasElement | null>(null);
  const [layers, setLayers] = useState<OverlayLayers>(DEFAULT_LAYERS);
  const [bannerHidden, setBannerHidden] = useState(false);
  const stageRef = useRef<HTMLDivElement>(null);

  const mapLabels = useMemo(
    () => ({
      lowConfidence: t('player.mapLowConfidence'),
      notAvailable: (shot: ShotType) =>
        t('player.mapNotAvailable', { shot: tc(`status.shot.${shot}`).toLowerCase() }),
    }),
    [t, tc],
  );
  const { clock } = useSyncEngine({
    manifest: manifest.data ?? null,
    video: videoEl,
    overlayCanvas: overlayEl,
    mapCanvas: mapEl,
    mapLabels,
    layers,
  });
  const state = useClockState(clock);
  const m = manifest.data;
  const fps = m?.fps ?? 25;
  const frame = state?.frameIdx ?? 0;
  const currentMs = msForFrame(frame, fps);

  // ?t=<seconds>: seek once the clock exists; write it back when paused (shareable position).
  const seeded = useRef(false);
  useEffect(() => {
    if (clock && !seeded.current && startSeconds !== undefined) {
      seeded.current = true;
      clock.seekToMs(startSeconds * 1000);
    }
  }, [clock, startSeconds]);
  useEffect(() => {
    if (!state || state.playing || state.seeking || !clock) return;
    const s = Math.round(currentMs / 1000);
    if (s !== startSeconds) void navigate({ search: (prev) => ({ ...prev, t: s }), replace: true });
  }, [state?.playing, state?.seeking, currentMs]); // eslint-disable-line react-hooks/exhaustive-deps

  // E08–E12 keyboard: Space play/pause · , . frame step (Shift ±5 s) · F fullscreen · 1 overview tab.
  useEffect(() => {
    if (!clock) return;
    const onKey = (e: KeyboardEvent) => {
      if (e.defaultPrevented || e.metaKey || e.ctrlKey || e.altKey || isTypingTarget(e.target))
        return;
      if (document.querySelector('[role="dialog"]')) return;
      if (e.key === ' ') clock.togglePlay();
      else if (e.key === ',' || e.key === '<') clock.step(e.shiftKey ? -5 * fps : -1);
      else if (e.key === '.' || e.key === '>') clock.step(e.shiftKey ? 5 * fps : 1);
      else if (e.key === 'f' || e.key === 'F') void stageRef.current?.requestFullscreen();
      else return;
      e.preventDefault();
    };
    window.addEventListener('keydown', onKey);
    return () => window.removeEventListener('keydown', onKey);
  }, [clock, fps]);

  const title = video.data?.title ?? videoId;
  const pitch = m?.pitch.status;
  const notReady = manifest.isError || media.isError;
  // SCR-14 / SCR-15 come back here (same tab and position).
  const here = `/videos/${encodeURIComponent(videoId)}/${tab}${startSeconds !== undefined ? `?t=${String(startSeconds)}` : ''}`;
  const calibrateSearch = {
    t: state ? Math.round(currentMs / 1000) : startSeconds,
    return_to: here,
  };

  return (
    <div className="mx-auto max-w-[1600px] px-5 pt-4 pb-[60px]">
      <nav
        aria-label={t('workspace.breadcrumb')}
        className="mb-3 flex items-center gap-2.5 text-md"
      >
        <Link to="/library" className="rounded-xs font-semibold text-fg-2 focus-ring hover:text-fg">
          {t('workspace.crumb')}
        </Link>
        <ChevronRight size={16} className="text-fg-3" aria-hidden="true" />
        <b className="min-w-0 truncate text-lg font-bold">{title}</b>
        <div className="flex-1" />
        {m && (
          <Button
            size="sm"
            onClick={() =>
              void navigate({ search: (prev) => ({ ...prev, export: 'video' as const }) })
            }
          >
            <Download size={15} aria-hidden="true" />
            {t('workspace.export')}
          </Button>
        )}
        {/* < 768 px the workspace is view-only (UIUX §09): no team / calibration entry points */}
        {canEdit && (
          <div className="hidden md:block">
            <DropdownMenu modal={false}>
              <DropdownMenuTrigger asChild>
                <IconButton
                  label={t('workspace.more')}
                  tooltip={false}
                  icon={<MoreVertical size={17} />}
                />
              </DropdownMenuTrigger>
              <DropdownMenuContent>
                <DropdownMenuItem
                  icon={<Shirt size={16} />}
                  onSelect={() =>
                    void navigate({
                      to: '/videos/$videoId/teams',
                      params: { videoId },
                      search: { return_to: here },
                    })
                  }
                >
                  {t('workspace.teamsColors')}
                </DropdownMenuItem>
                <DropdownMenuItem
                  icon={<Crosshair size={16} />}
                  onSelect={() =>
                    void navigate({
                      to: '/videos/$videoId/calibrate',
                      params: { videoId },
                      search: calibrateSearch,
                    })
                  }
                >
                  {t('workspace.calibratePitch')}
                </DropdownMenuItem>
              </DropdownMenuContent>
            </DropdownMenu>
          </div>
        )}
      </nav>

      {m && exportTab && (
        <ExportDialog
          open
          onOpenChange={(open) => {
            if (!open)
              void navigate({ search: (prev) => ({ ...prev, export: undefined }), replace: true });
          }}
          videoId={videoId}
          manifest={m}
          layers={layers}
          currentMs={currentMs}
        />
      )}

      {notReady && (
        <Banner
          tone="info"
          actions={
            <Button asChild>
              <Link to="/videos/$videoId/processing" params={{ videoId }}>
                {t('workspace.openProcessing')}
              </Link>
            </Button>
          }
        >
          {t('workspace.notReady')}
        </Banner>
      )}

      {!bannerHidden && (pitch === 'degraded' || pitch === 'unavailable') && (
        <Banner
          tone="warn"
          className="mb-3"
          actions={
            <>
              {canEdit && meta.data?.pitch_model && (
                <Button
                  size="sm"
                  variant="primary"
                  className="hidden md:inline-flex"
                  loading={repitch.isPending}
                  onClick={() => repitch.mutate()}
                >
                  {t('workspace.mapPitch')}
                </Button>
              )}
              {canEdit && (
                <Button asChild size="sm" className="hidden md:inline-flex">
                  <Link
                    to="/videos/$videoId/calibrate"
                    params={{ videoId }}
                    search={calibrateSearch}
                  >
                    {t('workspace.calibrate')}
                  </Link>
                </Button>
              )}
              <Button variant="ghost" onClick={() => setBannerHidden(true)}>
                {tc('actions.close')}
              </Button>
            </>
          }
        >
          {t(pitch === 'degraded' ? 'workspace.pitchDegraded' : 'workspace.pitchUnavailable')}
        </Banner>
      )}

      {!notReady && (
        <div className="mb-3 lg:hidden">
          {/* 768–1023 px and phones (UIUX §09): video above the map, pick what to show */}
          <SegmentedControl<Pane>
            aria-label={t('workspace.pane.label')}
            value={pane}
            onValueChange={setPane}
            options={PANES.map((p) => ({ value: p, label: t(`workspace.pane.${p}`) }))}
          />
        </div>
      )}
      {!notReady && (
        <div className="grid gap-4 lg:grid-cols-[minmax(0,1fr)_420px]">
          <div className={cn('flex min-w-0 flex-col gap-2', pane === 'map' && 'max-lg:hidden')}>
            <p className="m-0 font-mono text-caption text-fg-3" aria-live="off">
              {t('workspace.frameInfo', { fps, frame, total: m?.frame_count ?? 0 })}
              {state?.approximate ? ` · ${t('workspace.approximateSync')}` : ''}
            </p>
            <div
              ref={stageRef}
              className="relative aspect-video overflow-hidden rounded-xl bg-black"
            >
              {media.data ? (
                <>
                  <video
                    ref={setVideoEl}
                    src={media.data.video_url}
                    className="absolute inset-0 h-full w-full"
                    playsInline
                    preload="auto"
                    crossOrigin="anonymous"
                    onError={() => void media.refetch()} // expired signed URL
                    data-testid="workspace-video"
                  />
                  <canvas
                    ref={setOverlayEl}
                    className="pointer-events-none absolute inset-0 h-full w-full"
                  />
                </>
              ) : (
                <Skeleton className="h-full w-full" />
              )}
            </div>
            <div className="flex flex-wrap items-center gap-2">
              <IconButton
                label={state?.playing ? t('player.pause') : t('player.play')}
                icon={state?.playing ? <Pause size={18} /> : <Play size={18} />}
                onClick={() => clock?.togglePlay()}
                disabled={!clock}
              />
              <IconButton
                label={t('player.prevFrame')}
                icon={<SkipBack size={16} />}
                onClick={() => clock?.step(-1)}
                disabled={!clock}
              />
              <IconButton
                label={t('player.nextFrame')}
                icon={<SkipForward size={16} />}
                onClick={() => clock?.step(1)}
                disabled={!clock}
              />
              <Select
                aria-label={t('player.speed')}
                value={String(state?.rate ?? 1)}
                onValueChange={(v) => clock?.setRate(Number(v))}
                options={SPEEDS.map((s) => ({ value: s, label: `${s}×` }))}
              />
              <span className="font-mono text-caption text-fg-2">
                {fmt.timecode(currentMs)} / {fmt.timecode(m?.duration_ms ?? 0)}
              </span>
              <div className="flex-1" />
              <IconButton
                label={t('player.fullscreen')}
                icon={<Maximize size={16} />}
                onClick={() => void stageRef.current?.requestFullscreen()}
              />
            </div>
            {m && (
              <ShotTimeline
                shots={m.shots}
                durationMs={m.duration_ms}
                currentMs={currentMs}
                onSeek={(ms) => clock?.seekToMs(ms)}
              />
            )}
            <fieldset className="m-0 flex flex-wrap gap-4 border-0 p-0">
              <legend className="mb-1 text-caption font-semibold text-fg-2">
                {t('player.layers')}
              </legend>
              {LAYER_KEYS.map((k) => (
                <Checkbox
                  key={k}
                  label={t(`player.layer.${k}`)}
                  checked={layers[k]}
                  onCheckedChange={(c) => setLayers((l) => ({ ...l, [k]: c === true }))}
                />
              ))}
            </fieldset>
          </div>
          <Card
            className={cn('flex flex-col gap-2 self-start', pane === 'video' && 'max-lg:hidden')}
          >
            <h2 className="m-0 text-caption font-semibold tracking-wide text-fg-2 uppercase">
              {t('player.tacticalMap')}
            </h2>
            <canvas
              ref={setMapEl}
              className="aspect-[105/68] w-full rounded-lg"
              data-testid="tactical-map"
            />
          </Card>
        </div>
      )}

      <Tabs
        className="mt-5"
        value={tab}
        onValueChange={(v) =>
          void navigate({ params: { videoId, tab: v as WorkspaceTab }, search: (s) => s })
        }
      >
        <TabsList aria-label={t('workspace.tabs.label')}>
          {WORKSPACE_TABS.map((key) => (
            <TabsTrigger key={key} value={key}>
              {t(`workspace.tabs.${key}`)}
            </TabsTrigger>
          ))}
        </TabsList>
        <TabsContent value="overview">
          <Card className="grid gap-3">
            {m ? (
              <>
                <div className="text-body-sm flex flex-wrap gap-6">
                  {m.teams.map((team) => (
                    <span key={team.cluster} className="inline-flex items-center gap-2">
                      <span
                        aria-hidden="true"
                        className="h-3 w-3 rounded-full"
                        style={{ background: team.color }}
                      />
                      <b>{team.name}</b>
                      {!team.confirmed && (
                        <span className="text-caption text-fg-3">
                          {t('workspace.teamUnconfirmed')}
                        </span>
                      )}
                    </span>
                  ))}
                </div>
                {canEdit && m.teams.some((team) => !team.confirmed) && (
                  <Button asChild size="sm" className="hidden self-start md:inline-flex">
                    <Link
                      to="/videos/$videoId/teams"
                      params={{ videoId }}
                      search={{ return_to: here }}
                    >
                      {t('workspace.confirmTeams')}
                    </Link>
                  </Button>
                )}
                <p className="text-body-sm m-0 text-fg-2">
                  {t('workspace.quality', {
                    coverage: Math.round(m.coverage.main_wide_pct),
                    pitch: t(`workspace.pitch.${m.pitch.status}`),
                  })}
                </p>
                <p className="m-0 text-caption text-fg-3">{t('workspace.statsSoon')}</p>
              </>
            ) : (
              <Skeleton className="h-16" />
            )}
          </Card>
        </TabsContent>
      </Tabs>
    </div>
  );
}
