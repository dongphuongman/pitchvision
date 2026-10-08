import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { getRouteApi, Link } from '@tanstack/react-router';
import {
  Download,
  ChevronRight,
  Crosshair,
  Maximize,
  MoreVertical,
  MousePointerClick,
  Pause,
  Play,
  Shirt,
  SkipBack,
  SkipForward,
} from 'lucide-react';
import { useCallback, useEffect, useMemo, useRef, useState } from 'react';
import { useTranslation } from 'react-i18next';
import { applyCorrections, postRepitch, type CorrectionOut } from '@/api/corrections';
import { useMeta } from '@/api/queries';
import { manifestQuery, mediaQuery } from '@/api/results';
import type { ShotType, Tile } from '@/api/types';
import { videoQuery } from '@/api/videos';
import { can } from '@/auth/roles';
import { ExportDialog } from '@/features/exports/ExportDialog';
import { useRole } from '@/auth/session';
import { useFormat } from '@/i18n/format';
import { cn } from '@/lib/cn';
import { applyCorrectionsToTile } from '@/player/edits';
import { msForFrame } from '@/player/frames';
import { DEFAULT_LAYERS, fitContain, type OverlayLayers } from '@/player/overlay';
import { OBJ, OBJ_CLS } from '@/player/tileCodec';
import { mapTransform } from '@/player/tacticalMap';
import { ShotTimeline } from '@/player/ShotTimeline';
import { useClockState, useSyncEngine } from '@/player/useSyncEngine';
import {
  CorrectionsBadge,
  TrackCorrectionMenu,
  type TrackMenuAnchor,
} from '@/features/video/TrackCorrections';
import { pickMapTrack, pickVideoTrack, useCorrectionsState } from '@/features/video/trackEdits';
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
import { DataTab } from './DataTab';
import { HeatmapsTab } from './HeatmapsTab';
import { PassingTab } from './PassingTab';
import { StatsPanel } from './StatsPanel';
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
/** Stable empty list so the optimistic TileStore transform survives re-renders. */
const NO_CORRECTIONS: CorrectionOut[] = [];

/** SCR-20 Analysis workspace (M1-FE-12): video + overlay ↔ tactical map, shot timeline, tabs. */
export function WorkspacePage() {
  const { videoId, tab } = route.useParams();
  const search = route.useSearch();
  const { t: startSeconds, export: exportTab } = search;
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
  const queryClient = useQueryClient();
  // SCR-22 live corrections (editors only need to write; reading is cheap for everyone).
  const corrections = useCorrectionsState(videoId, canEdit);

  const [videoEl, setVideoEl] = useState<HTMLVideoElement | null>(null);
  const [overlayEl, setOverlayEl] = useState<HTMLCanvasElement | null>(null);
  const [mapEl, setMapEl] = useState<HTMLCanvasElement | null>(null);
  const [layers, setLayers] = useState<OverlayLayers>(DEFAULT_LAYERS);
  const [bannerHidden, setBannerHidden] = useState(false);
  // SCR-22 menu anchor (viewport coords) + merge-pick mode.
  const [menu, setMenu] = useState<TrackMenuAnchor | null>(null);
  const [mergeFrom, setMergeFrom] = useState<number | null>(null);
  const stageRef = useRef<HTMLDivElement>(null);
  const touchTimer = useRef<number | undefined>(undefined);

  // Optimistic tiles: re-apply the live list whenever it changes. react-query keeps
  // the items reference stable while the payload is equal, so the TileStore (and its
  // cached tiles) survives the 5 s polls.
  const correctionItems = corrections.state?.items ?? NO_CORRECTIONS;
  const correctionsTransform = useMemo(
    () =>
      correctionItems.length > 0
        ? (tile: Tile) => applyCorrectionsToTile(tile, correctionItems)
        : undefined,
    [correctionItems],
  );

  const mapLabels = useMemo(
    () => ({
      lowConfidence: t('player.mapLowConfidence'),
      notAvailable: (shot: ShotType) =>
        t('player.mapNotAvailable', { shot: tc(`status.shot.${shot}`).toLowerCase() }),
    }),
    [t, tc],
  );
  const { clock, store } = useSyncEngine({
    manifest: manifest.data ?? null,
    video: videoEl,
    overlayCanvas: overlayEl,
    mapCanvas: mapEl,
    mapLabels,
    layers,
    transform: correctionsTransform,
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
      else if (e.key === 'Escape' && mergeFrom !== null) setMergeFrom(null);
      else return;
      e.preventDefault();
    };
    window.addEventListener('keydown', onKey);
    return () => window.removeEventListener('keydown', onKey);
  }, [clock, fps, mergeFrom]);

  /* ------------------------------------------------ SCR-22 track picking */
  const frameMs = Math.round(msForFrame(frame, fps));
  const frameLenMs = Math.max(1, Math.round(1000 / fps));

  const teamLabelFor = useCallback(
    (trackId: number): string => {
      const lookup = store?.frame(frame);
      const row = lookup?.frame?.objs?.find((o) => o[OBJ.trackId] === trackId);
      if (row && row[OBJ.cls] === OBJ_CLS.referee) return t('trackEdits.referee', 'Referee');
      const cluster = row?.[OBJ.team];
      const team = m?.teams.find((x) => x.cluster === cluster);
      return team?.name ?? t('trackEdits.unknownTeam', 'Unknown team');
    },
    [store, frame, m?.teams, t],
  );

  const pickAtVideo = useCallback(
    (clientX: number, clientY: number): number | null => {
      const stage = stageRef.current;
      if (!stage || !m || !store) return null;
      const box = stage.getBoundingClientRect();
      const rect = fitContain(box.width, box.height, m.width, m.height);
      if (rect.scale <= 0) return null;
      const px = (clientX - box.left - rect.offsetX) / rect.scale;
      const py = (clientY - box.top - rect.offsetY) / rect.scale;
      return pickVideoTrack(store.frame(frame).frame?.objs, px, py);
    },
    [store, frame, m],
  );

  const pickAtMap = useCallback(
    (clientX: number, clientY: number): number | null => {
      if (!mapEl || !store) return null;
      const box = mapEl.getBoundingClientRect();
      const tr = mapTransform(box.width, box.height);
      if (tr.scale <= 0) return null;
      const mx = (clientX - box.left - tr.ox) / tr.scale;
      const my = (clientY - box.top - tr.oy) / tr.scale;
      const dots = (store.frame(frame).frame?.objs ?? [])
        .filter((o) => o[OBJ.cls] !== OBJ_CLS.ball && o[OBJ.px] != null && o[OBJ.py] != null)
        .map((o) => ({ trackId: o[OBJ.trackId], x: o[OBJ.px] as number, y: o[OBJ.py] as number }));
      return pickMapTrack(dots, mx, my);
    },
    [mapEl, store, frame],
  );

  const openMenuAt = useCallback(
    (trackId: number, clientX: number, clientY: number, source: 'video' | 'map') => {
      setMenu({ trackId, x: clientX, y: clientY, source });
    },
    [],
  );

  // Blocked corrections (another job ran past them): the client starts the edit job
  // once that job has finished (contract: pending && !job → POST /corrections/apply).
  const appliedForJob = useRef<string | null>(null);
  const latestJob = video.data?.latest_job;
  useEffect(() => {
    const st = corrections.state;
    if (!canEdit || !st || !st.pending || st.job || !latestJob) return;
    if (latestJob.status !== 'done' && latestJob.status !== 'failed') return;
    if (appliedForJob.current === latestJob.id) return;
    appliedForJob.current = latestJob.id;
    void applyCorrections(videoId).then(() =>
      queryClient.invalidateQueries({ queryKey: ['results', videoId, 'corrections'] }),
    );
  }, [canEdit, corrections.state, latestJob, queryClient, videoId]);

  const openHeatmap = useCallback(
    (trackId: number) => {
      void navigate({
        params: { videoId, tab: 'heatmaps' },
        search: (s) => ({ ...s, track: trackId }),
      });
    },
    [navigate, videoId],
  );

  const cancelTouch = useCallback(() => {
    window.clearTimeout(touchTimer.current);
    touchTimer.current = undefined;
  }, []);
  useEffect(() => () => window.clearTimeout(touchTimer.current), []);

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
              void navigate({
                search: (prev) => ({ ...prev, export: undefined, format: undefined }),
                replace: true,
              });
          }}
          kind={exportTab}
          dataFormat={search.format}
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
            <p
              className="m-0 flex flex-wrap items-center gap-2 font-mono text-caption text-fg-3"
              aria-live="off"
            >
              <span>
                {t('workspace.frameInfo', { fps, frame, total: m?.frame_count ?? 0 })}
                {state?.approximate ? ` · ${t('workspace.approximateSync')}` : ''}
              </span>
              {canEdit && <CorrectionsBadge videoId={videoId} state={corrections.state} />}
            </p>
            {canEdit && mergeFrom !== null && (
              <Banner
                tone="info"
                className="mb-1"
                actions={
                  <Button variant="ghost" size="sm" onClick={() => setMergeFrom(null)}>
                    {tc('actions.cancel')}
                  </Button>
                }
              >
                <span className="inline-flex items-center gap-1.5">
                  <MousePointerClick size={15} aria-hidden="true" />
                  {t('trackEdits.mergePicking', 'Click the same player elsewhere · Esc to cancel')}
                </span>
              </Banner>
            )}
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
                  {canEdit && (
                    <div
                      role="application"
                      aria-label={t('trackEdits.title', 'Fix track')}
                      className={cn(
                        'absolute inset-0 touch-none',
                        mergeFrom !== null && 'cursor-crosshair',
                      )}
                      style={{ WebkitTouchCallout: 'none' }}
                      onContextMenu={(e) => {
                        e.preventDefault();
                        const hit = pickAtVideo(e.clientX, e.clientY);
                        if (hit !== null) openMenuAt(hit, e.clientX, e.clientY, 'video');
                      }}
                      onClick={(e) => {
                        if (mergeFrom === null) return;
                        const hit = pickAtVideo(e.clientX, e.clientY);
                        if (hit !== null && hit !== mergeFrom) {
                          openMenuAt(hit, e.clientX, e.clientY, 'video');
                        }
                      }}
                      onTouchStart={(e) => {
                        const touch = e.touches[0];
                        if (!touch) return;
                        const { clientX, clientY } = touch;
                        cancelTouch();
                        touchTimer.current = window.setTimeout(() => {
                          const hit = pickAtVideo(clientX, clientY);
                          if (hit !== null) {
                            if (mergeFrom !== null && hit !== mergeFrom) {
                              openMenuAt(hit, clientX, clientY, 'video');
                            } else if (mergeFrom === null) {
                              openMenuAt(hit, clientX, clientY, 'video');
                            }
                          }
                        }, 500);
                      }}
                      onTouchMove={cancelTouch}
                      onTouchEnd={cancelTouch}
                    />
                  )}
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
            <div
              className={cn('relative', canEdit && mergeFrom !== null && 'cursor-crosshair')}
              onContextMenu={
                canEdit
                  ? (e) => {
                      e.preventDefault();
                      const hit = pickAtMap(e.clientX, e.clientY);
                      if (hit !== null) openMenuAt(hit, e.clientX, e.clientY, 'map');
                    }
                  : undefined
              }
              onClick={
                canEdit && mergeFrom !== null
                  ? (e) => {
                      const hit = pickAtMap(e.clientX, e.clientY);
                      if (hit !== null && hit !== mergeFrom) {
                        openMenuAt(hit, e.clientX, e.clientY, 'map');
                      }
                    }
                  : undefined
              }
            >
              <canvas
                ref={setMapEl}
                className="aspect-[105/68] w-full rounded-lg"
                data-testid="tactical-map"
              />
            </div>
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
                <StatsPanel
                  videoId={videoId}
                  revision={m.revision}
                  teams={m.teams}
                  mainWidePct={m.coverage.main_wide_pct}
                  onSeek={(ms) => clock?.seekToMs(ms)}
                />
              </>
            ) : (
              <Skeleton className="h-16" />
            )}
          </Card>
        </TabsContent>
        <TabsContent value="data">
          {m ? (
            <DataTab
              manifest={m}
              onExport={(format) =>
                void navigate({ search: (prev) => ({ ...prev, export: 'data', format }) })
              }
            />
          ) : (
            <Skeleton className="h-40" />
          )}
        </TabsContent>
        <TabsContent value="passing">
          {m ? (
            <PassingTab
              videoId={videoId}
              revision={m.revision}
              teams={m.teams}
              team={search.team}
              onTeam={(team) =>
                void navigate({ search: (prev) => ({ ...prev, team }), replace: true })
              }
              onSeek={(ms) => clock?.seekToMs(ms)}
            />
          ) : (
            <Skeleton className="h-80" />
          )}
        </TabsContent>
        <TabsContent value="heatmaps">
          {m ? (
            <HeatmapsTab
              videoId={videoId}
              revision={m.revision}
              teams={m.teams}
              team={search.team}
              track={search.track}
              avg={search.avg === 1}
              title={title}
              onChange={(next) =>
                void navigate({
                  search: (prev) => ({
                    ...prev,
                    team: next.team,
                    track: next.track,
                    avg: next.avg,
                  }),
                  replace: true,
                })
              }
            />
          ) : (
            <Skeleton className="h-80" />
          )}
        </TabsContent>
      </Tabs>

      {canEdit && menu && m && (
        <>
          <div
            aria-hidden="true"
            className="fixed inset-0 z-[89]"
            onClick={() => setMenu(null)}
            onContextMenu={(e) => {
              e.preventDefault();
              setMenu(null);
            }}
          />
          <TrackCorrectionMenu
            videoId={videoId}
            anchor={menu}
            teamLabel={teamLabelFor(menu.trackId)}
            teams={m.teams}
            frameMs={frameMs}
            frameLenMs={frameLenMs}
            mergeFrom={mergeFrom}
            onEnterMerge={(from) => {
              setMenu(null);
              setMergeFrom(from);
            }}
            onOpenHeatmap={openHeatmap}
            onClose={() => {
              setMenu(null);
              setMergeFrom(null);
            }}
          />
        </>
      )}
    </div>
  );
}
