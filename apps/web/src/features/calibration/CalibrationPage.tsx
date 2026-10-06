import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { getRouteApi, Link } from '@tanstack/react-router';
import { ChevronLeft, ChevronRight, ChevronsLeft, ChevronsRight, X } from 'lucide-react';
import { useMemo, useRef, useState, type KeyboardEvent, type MouseEvent } from 'react';
import { useTranslation } from 'react-i18next';
import { Page } from '@/app/Page';
import {
  adoptJob,
  CALIBRATION_REASONS,
  calibrationQuery,
  FRAME_MS,
  frameQuery,
  pitchTemplateQuery,
  postCalibration,
  problemReason,
  snapMs,
  type CalibrationScope,
  type ManualCalibration,
  type PitchTemplate,
  type TemplateKeypoint,
} from '@/api/corrections';
import { manifestQuery } from '@/api/results';
import type { Shot } from '@/api/types';
import { can } from '@/auth/roles';
import { useRole } from '@/auth/session';
import { BackLink, CorrectionError } from '@/features/video/corrections';
import { formatTimecode, useFormat } from '@/i18n/format';
import { cn } from '@/lib/cn';
import {
  Badge,
  Banner,
  Button,
  Card,
  Checkbox,
  IconButton,
  SegmentedControl,
  Skeleton,
  toast,
} from '@/ui';
import { fitHomography, generalPosition, project, type Pt } from './geometry';

const route = getRouteApi('/_app/videos/$videoId/calibrate');

const MIN_POINTS = 4;
const MAX_POINTS = 8;
const PITCH_MARGIN = 4;

interface Point {
  template_index: number;
  x: number;
  y: number;
}

function isReason(r: string): r is (typeof CALIBRATION_REASONS)[number] {
  return (CALIBRATION_REASONS as readonly string[]).includes(r);
}

/** Middle of the longest main_wide shot (default frame when the URL has no `?t=`). */
function defaultFrameMs(shots: readonly Shot[]): number {
  const wide = shots.filter((s) => s.type === 'main_wide');
  const longest = wide.reduce<Shot | undefined>(
    (best, s) => (!best || s.t1_ms - s.t0_ms > best.t1_ms - best.t0_ms ? s : best),
    undefined,
  );
  return longest ? (longest.t0_ms + longest.t1_ms) / 2 : 0;
}

/** SCR-15 Manual pitch calibration: match 4–8 frame pixels to pitch-template landmarks. */
export function CalibrationPage() {
  const { videoId } = route.useParams();
  const { t: startSeconds, return_to } = route.useSearch();
  const navigate = route.useNavigate();
  const { t } = useTranslation('video');
  const fmt = useFormat();
  const qc = useQueryClient();
  const canEdit = can(useRole(), 'editor');
  const back = return_to ?? `/videos/${encodeURIComponent(videoId)}/overview`;

  const state = useQuery(calibrationQuery(videoId));
  const template = useQuery(pitchTemplateQuery());
  const manifest = useQuery(manifestQuery(videoId));

  const [chosenMs, setChosenMs] = useState<number | null>(null);
  const [points, setPoints] = useState<Point[]>([]);
  const [landmark, setLandmark] = useState<number | null>(null);
  const [active, setActive] = useState<number | null>(null);
  const [scope, setScope] = useState<CalibrationScope>('shot');
  const [preview, setPreview] = useState(true);
  const [notice, setNotice] = useState('');

  const initialMs =
    startSeconds !== undefined
      ? startSeconds * 1000
      : manifest.data
        ? defaultFrameMs(manifest.data.shots)
        : manifest.isError
          ? 0
          : null;
  const frameMs = chosenMs ?? (initialMs === null ? null : snapMs(initialMs));
  const frame = useQuery({ ...frameQuery(videoId, frameMs ?? 0), enabled: frameMs !== null });
  const videoW = manifest.data?.width ?? 1280;
  const videoH = manifest.data?.height ?? 720;
  const durationMs = manifest.data?.duration_ms ?? null;
  const wide = useMemo(
    () => (manifest.data?.shots ?? []).filter((s) => s.type === 'main_wide'),
    [manifest.data],
  );

  const submit = useMutation({
    mutationFn: (body: { points: Point[]; frameMs: number }) =>
      postCalibration(
        videoId,
        body.points.map((p) => ({ ...p, frame_ms: body.frameMs })),
        scope,
      ),
    onSuccess: (res) => {
      adoptJob(qc, videoId, res.job);
      void qc.invalidateQueries({ queryKey: calibrationQuery(videoId).queryKey });
      toast({ title: t('calibrate.started'), tone: 'info' });
      void navigate({ to: '/videos/$videoId/processing', params: { videoId } });
    },
  });

  const keypoints = template.data?.keypoints ?? [];
  const kpByIndex = new Map(keypoints.map((k) => [k.index, k]));
  const nameOf = (index: number) => {
    const kp = kpByIndex.get(index);
    return kp ? t(`calibrate.landmark.${kp.name}`, { defaultValue: kp.name }) : `#${String(index)}`;
  };

  /** Moves to another frame; points belong to one frame, so they are cleared (unless kept). */
  const goTo = (ms: number, keep: Point[] | null = null) => {
    const max = durationMs !== null ? Math.max(0, durationMs - FRAME_MS) : Infinity;
    setChosenMs(snapMs(Math.min(Math.max(0, ms), max)));
    setActive(null);
    submit.reset();
    if (keep) {
      setPoints(keep);
      setNotice('');
    } else if (points.length > 0) {
      setPoints([]);
      setNotice(t('calibrate.cleared'));
    }
  };

  const place = (index: number, x: number, y: number) => {
    const px = Math.round(Math.min(Math.max(0, x), videoW) * 10) / 10;
    const py = Math.round(Math.min(Math.max(0, y), videoH) * 10) / 10;
    const exists = points.some((p) => p.template_index === index);
    if (!exists && points.length >= MAX_POINTS) {
      setNotice(t('calibrate.max'));
      return;
    }
    setPoints(
      exists
        ? points.map((p) => (p.template_index === index ? { ...p, x: px, y: py } : p))
        : [...points, { template_index: index, x: px, y: py }],
    );
    setActive(index);
    setLandmark(null);
    setNotice('');
    submit.reset();
  };

  const nudge = (dx: number, dy: number) => {
    const p = points.find((q) => q.template_index === active);
    if (p) place(p.template_index, p.x + dx, p.y + dy);
  };

  const onStageClick = (e: MouseEvent<HTMLDivElement>) => {
    if (!canEdit || landmark === null) return;
    const r = e.currentTarget.getBoundingClientRect();
    if (r.width <= 0 || r.height <= 0) return;
    place(
      landmark,
      ((e.clientX - r.left) / r.width) * videoW,
      ((e.clientY - r.top) / r.height) * videoH,
    );
  };

  const onStageKey = (e: KeyboardEvent<HTMLDivElement>) => {
    if (!canEdit) return;
    const step = e.shiftKey ? 10 : 1;
    const moves: Record<string, [number, number]> = {
      ArrowLeft: [-step, 0],
      ArrowRight: [step, 0],
      ArrowUp: [0, -step],
      ArrowDown: [0, step],
    };
    const move = moves[e.key];
    if (move && active !== null) {
      e.preventDefault();
      nudge(move[0], move[1]);
    } else if ((e.key === 'Enter' || e.key === ' ') && landmark !== null) {
      e.preventDefault();
      place(landmark, videoW / 2, videoH / 2);
    } else if ((e.key === 'Delete' || e.key === 'Backspace') && active !== null) {
      e.preventDefault();
      setPoints(points.filter((p) => p.template_index !== active));
      setActive(null);
    }
  };

  // Live validation (mirrors the API: ≥ 4 points, distinct landmarks, general position).
  const image = points.map((p): Pt => [p.x, p.y]);
  const pitch = points.map((p): Pt => {
    const k = kpByIndex.get(p.template_index);
    return [k?.x ?? 0, k?.y ?? 0];
  });
  const enough = points.length >= MIN_POINTS;
  const collinear = enough && (!generalPosition(image) || !generalPosition(pitch));
  const homography = enough && !collinear ? fitHomography(pitch, image) : null;
  const hint = !enough
    ? t('calibrate.needMore', { count: points.length })
    : collinear
      ? t('calibrate.collinear')
      : t('calibrate.ready', { count: points.length });

  const activeJob = state.data?.active_job ?? null;
  const pitchStatus = state.data?.pitch_calibration ?? manifest.data?.pitch.status ?? null;
  const canSubmit =
    canEdit && enough && !collinear && !!frame.data && !activeJob && !submit.isPending;

  const curShot =
    frameMs === null ? -1 : wide.findIndex((s) => s.t0_ms <= frameMs && frameMs < s.t1_ms);
  const prevShot =
    frameMs === null
      ? undefined
      : curShot >= 0
        ? wide[curShot - 1]
        : wide.filter((s) => s.t1_ms <= frameMs).at(-1);
  const nextShot =
    frameMs === null
      ? undefined
      : curShot >= 0
        ? wide[curShot + 1]
        : wide.find((s) => s.t0_ms > frameMs);
  const mid = (s: Shot) => (s.t0_ms + s.t1_ms) / 2;
  const frameReason = problemReason(frame.error);
  const reasonMessage = (r: string) => (isReason(r) ? t(`calibrate.reason.${r}`) : undefined);

  return (
    <Page>
      <BackLink href={back} />
      <div className="mt-2.5 mb-1 flex flex-wrap items-center gap-3">
        <h1 className="m-0">{t('calibrate.title')}</h1>
        {pitchStatus && (
          <Badge tone={pitchStatus === 'ok' || pitchStatus === 'manual' ? 'ok' : 'warn'}>
            {t('calibrate.quality', { status: t(`workspace.pitch.${pitchStatus}`) })}
          </Badge>
        )}
      </div>
      <p className="mt-0 mb-4 text-fg-2">{t('calibrate.intro')}</p>

      <div className="flex flex-col gap-3">
        {!canEdit && <Banner tone="info">{t('calibrate.readOnly')}</Banner>}
        {activeJob && (
          <Banner
            tone="warn"
            actions={
              <Button asChild size="sm">
                <Link to="/videos/$videoId/processing" params={{ videoId }}>
                  {t('corrections.openProcessing')}
                </Link>
              </Button>
            }
          >
            {t('calibrate.activeJob')}
          </Banner>
        )}
        {state.data?.last_manual && (
          <LastManual
            last={state.data.last_manual}
            onUse={
              canEdit
                ? (last) =>
                    goTo(
                      last.frame_ms,
                      last.points.map(({ template_index, x, y }) => ({ template_index, x, y })),
                    )
                : undefined
            }
          />
        )}
      </div>

      <div className="mt-4 grid gap-4 lg:grid-cols-[minmax(0,1fr)_380px]">
        <div className="flex min-w-0 flex-col gap-2">
          <div
            role="application"
            tabIndex={0}
            aria-label={t('calibrate.stage', { time: formatTimecode(frameMs ?? 0) })}
            aria-describedby="calibrate-hint"
            onClick={onStageClick}
            onKeyDown={onStageKey}
            data-testid="calibration-stage"
            className={cn(
              'relative w-full overflow-hidden rounded-xl bg-black focus-ring',
              canEdit && landmark !== null && 'cursor-crosshair',
            )}
            style={{ aspectRatio: `${videoW} / ${videoH}` }}
          >
            {frame.data ? (
              <img
                src={frame.data.url}
                alt=""
                draggable={false}
                className="pointer-events-none absolute inset-0 h-full w-full select-none"
              />
            ) : frame.isError ? (
              <div className="absolute inset-0 grid place-items-center gap-2 p-4 text-center text-sm text-white">
                <p className="m-0" role="alert">
                  {frameReason === 'no_video' || frameReason === 'out_of_range'
                    ? t(`calibrate.frameError.${frameReason}`)
                    : t('calibrate.frameError.generic')}
                </p>
              </div>
            ) : (
              <Skeleton className="absolute inset-0 h-full w-full" />
            )}
            {preview && homography && template.data && (
              <PitchOverlay
                template={template.data}
                h={homography}
                width={videoW}
                height={videoH}
              />
            )}
            {points.map((p, i) => (
              <span
                key={p.template_index}
                aria-hidden="true"
                className={cn(
                  'pointer-events-none absolute grid h-6 w-6 -translate-x-1/2 -translate-y-1/2 place-items-center rounded-full border-2 text-[11px] font-extrabold text-white',
                  active === p.template_index
                    ? 'border-white bg-accent text-accent-ink'
                    : 'border-accent bg-[color-mix(in_srgb,var(--ac)_35%,transparent)]',
                )}
                style={{ left: `${(p.x / videoW) * 100}%`, top: `${(p.y / videoH) * 100}%` }}
              >
                {i + 1}
              </span>
            ))}
          </div>
          <div className="flex flex-wrap items-center gap-2">
            <IconButton
              label={t('calibrate.prevShot')}
              icon={<ChevronsLeft size={16} />}
              disabled={!prevShot}
              onClick={() => prevShot && goTo(mid(prevShot))}
            />
            <IconButton
              label={t('calibrate.back1s')}
              icon={<ChevronLeft size={16} />}
              disabled={frameMs === null || frameMs <= 0}
              onClick={() => frameMs !== null && goTo(frameMs - 1000)}
            />
            <span className="font-mono text-caption text-fg-2">
              {t('calibrate.frameAt', {
                time: fmt.timecode(frame.data?.t_ms ?? frameMs ?? 0),
                frame: Math.round((frame.data?.t_ms ?? frameMs ?? 0) / FRAME_MS),
              })}
            </span>
            <IconButton
              label={t('calibrate.fwd1s')}
              icon={<ChevronRight size={16} />}
              disabled={frameMs === null}
              onClick={() => frameMs !== null && goTo(frameMs + 1000)}
            />
            <IconButton
              label={t('calibrate.nextShot')}
              icon={<ChevronsRight size={16} />}
              disabled={!nextShot}
              onClick={() => nextShot && goTo(mid(nextShot))}
            />
            <div className="flex-1" />
            <Checkbox
              label={t('calibrate.preview')}
              checked={preview}
              onCheckedChange={(c) => setPreview(c === true)}
            />
          </div>
        </div>

        <div className="flex flex-col gap-3">
          <Card className="flex flex-col gap-2">
            <h2 className="m-0 text-sm font-bold tracking-[.06em] text-fg-2 uppercase">
              {t('calibrate.diagram')}
            </h2>
            {template.data ? (
              <PitchDiagram
                template={template.data}
                selected={landmark}
                points={points}
                disabled={!canEdit}
                nameOf={nameOf}
                onSelect={(i) => {
                  setLandmark(i);
                  const used = points.some((p) => p.template_index === i);
                  if (used) setActive(i);
                }}
              />
            ) : (
              <Skeleton className="aspect-[113/76] w-full" />
            )}
            <p className="m-0 text-caption text-fg-2">
              {landmark === null
                ? t('calibrate.stepSelect')
                : t('calibrate.stepPlace', { name: nameOf(landmark) })}
            </p>
          </Card>

          <Card className="flex flex-col gap-2">
            <h2 className="m-0 text-sm font-bold tracking-[.06em] text-fg-2 uppercase">
              {t('calibrate.points', { count: points.length })}
            </h2>
            {points.length === 0 ? (
              <p className="m-0 text-caption text-fg-3">{t('calibrate.noPoints')}</p>
            ) : (
              <ol className="m-0 flex list-none flex-col gap-1.5 p-0">
                {points.map((p, i) => (
                  <li key={p.template_index} className="flex items-center gap-2">
                    <button
                      type="button"
                      aria-pressed={active === p.template_index}
                      onClick={() => setActive(p.template_index)}
                      className={cn(
                        'flex min-w-0 flex-1 items-center gap-2 rounded-md border px-2 py-1 text-left text-sm focus-ring',
                        active === p.template_index ? 'border-accent' : 'border-line',
                      )}
                    >
                      <span className="sr-only">
                        {t('calibrate.point', { n: i + 1, name: nameOf(p.template_index) })}
                      </span>
                      <span aria-hidden="true" className="flex min-w-0 flex-1 items-center gap-2">
                        <Badge tone="ok">{i + 1}</Badge>
                        <span className="truncate">{nameOf(p.template_index)}</span>
                      </span>
                      <span className="font-mono text-caption text-fg-3">
                        {Math.round(p.x)}, {Math.round(p.y)}
                      </span>
                    </button>
                    {canEdit && (
                      <IconButton
                        label={t('calibrate.remove', { n: i + 1, name: nameOf(p.template_index) })}
                        icon={<X size={14} />}
                        onClick={() => {
                          setPoints(points.filter((q) => q.template_index !== p.template_index));
                          if (active === p.template_index) setActive(null);
                          submit.reset();
                        }}
                      />
                    )}
                  </li>
                ))}
              </ol>
            )}
            <div aria-live="polite" id="calibrate-hint" className="flex flex-col gap-1">
              <p
                className={cn('m-0 text-caption', collinear ? 'text-warn' : 'text-fg-2')}
                data-testid="calibration-hint"
              >
                {hint}
              </p>
              {notice && <p className="m-0 text-caption text-fg-3">{notice}</p>}
            </div>
            {canEdit && points.length > 0 && (
              <Button
                variant="ghost"
                size="sm"
                className="self-start"
                onClick={() => {
                  setPoints([]);
                  setActive(null);
                  submit.reset();
                }}
              >
                {t('calibrate.reset')}
              </Button>
            )}
          </Card>

          <Card className="flex flex-col gap-2">
            <span id="calibrate-scope" className="text-caption font-semibold text-fg-2">
              {t('calibrate.scope')}
            </span>
            <SegmentedControl<CalibrationScope>
              aria-labelledby="calibrate-scope"
              aria-describedby="calibrate-scope-help"
              value={scope}
              disabled={!canEdit}
              onValueChange={setScope}
              options={[
                { value: 'shot', label: t('calibrate.scopeShot') },
                { value: 'video', label: t('calibrate.scopeVideo') },
              ]}
            />
            <p id="calibrate-scope-help" className="m-0 text-caption text-fg-3">
              {scope === 'shot' ? t('calibrate.scopeHelpShot') : t('calibrate.scopeHelpVideo')}
            </p>
          </Card>
        </div>
      </div>

      {submit.isError && (
        <div className="mt-4">
          <CorrectionError error={submit.error} videoId={videoId} reasons={reasonMessage} />
        </div>
      )}
      <div className="mt-4 flex flex-wrap justify-end gap-2">
        <Button variant="ghost" onClick={() => void navigate({ href: back })}>
          {t('corrections.cancel')}
        </Button>
        {canEdit && (
          <Button
            variant="primary"
            disabled={!canSubmit}
            onClick={() => {
              if (frame.data) submit.mutate({ points, frameMs: frame.data.t_ms });
            }}
          >
            {t('calibrate.submit')}
          </Button>
        )}
      </div>
    </Page>
  );
}

function LastManual({
  last,
  onUse,
}: {
  last: ManualCalibration;
  onUse?: (last: ManualCalibration) => void;
}) {
  const { t } = useTranslation('video');
  const fmt = useFormat();
  return (
    <Banner
      tone="info"
      actions={
        onUse ? (
          <Button size="sm" onClick={() => onUse(last)}>
            {t('calibrate.useLast')}
          </Button>
        ) : null
      }
    >
      {t('calibrate.last', {
        count: last.points.length,
        time: fmt.timecode(last.frame_ms),
        scope: last.scope === 'shot' ? t('calibrate.scopeShot') : t('calibrate.scopeVideo'),
        date: fmt.date(last.created_at),
      })}
    </Banner>
  );
}

/** SVG of the template (metres) with the 32 landmarks as a radio group (one tab stop, arrows). */
function PitchDiagram({
  template,
  selected,
  points,
  disabled,
  nameOf,
  onSelect,
}: {
  template: PitchTemplate;
  selected: number | null;
  points: Point[];
  disabled: boolean;
  nameOf: (index: number) => string;
  onSelect: (index: number) => void;
}) {
  const { t } = useTranslation('video');
  const refs = useRef(new Map<number, SVGGElement>());
  const kps = template.keypoints;
  const focusIndex = selected ?? kps[0]?.index ?? 0;
  const move = (from: TemplateKeypoint, delta: number) => {
    const i = kps.findIndex((k) => k.index === from.index);
    const next = kps[(i + delta + kps.length) % kps.length];
    if (!next) return;
    onSelect(next.index);
    refs.current.get(next.index)?.focus();
  };
  const m = PITCH_MARGIN;
  return (
    <svg
      role="radiogroup"
      aria-label={t('calibrate.diagram')}
      aria-disabled={disabled || undefined}
      viewBox={`${-m} ${-m} ${template.length_m + 2 * m} ${template.width_m + 2 * m}`}
      className="w-full rounded-lg bg-[#1f5f2c]"
    >
      <g fill="none" stroke="rgba(255,255,255,.75)" strokeWidth={0.35}>
        {template.lines.map((line) =>
          line.points.length === 1 ? (
            <circle
              key={line.name}
              cx={line.points[0]?.[0]}
              cy={line.points[0]?.[1]}
              r={0.4}
              fill="rgba(255,255,255,.75)"
            />
          ) : (
            <polyline
              key={line.name}
              points={(line.closed ? [...line.points, ...line.points.slice(0, 1)] : line.points)
                .map(([x, y]) => `${x},${y}`)
                .join(' ')}
            />
          ),
        )}
      </g>
      {kps.map((k) => {
        const n = points.findIndex((p) => p.template_index === k.index);
        const isSel = selected === k.index;
        const name = nameOf(k.index);
        return (
          <g
            key={k.index}
            ref={(el) => {
              if (el) refs.current.set(k.index, el);
              else refs.current.delete(k.index);
            }}
            role="radio"
            aria-checked={isSel}
            aria-disabled={disabled || undefined}
            aria-label={n >= 0 ? t('calibrate.landmarkUsed', { name, n: n + 1 }) : name}
            tabIndex={disabled ? -1 : k.index === focusIndex ? 0 : -1}
            className={cn('outline-none', !disabled && 'cursor-pointer')}
            onClick={() => !disabled && onSelect(k.index)}
            onKeyDown={(e) => {
              if (disabled) return;
              if (e.key === 'ArrowRight' || e.key === 'ArrowDown') move(k, 1);
              else if (e.key === 'ArrowLeft' || e.key === 'ArrowUp') move(k, -1);
              else if (e.key === ' ' || e.key === 'Enter') onSelect(k.index);
              else return;
              e.preventDefault();
            }}
          >
            <title>{name}</title>
            <circle
              cx={k.x}
              cy={k.y}
              r={isSel ? 2.2 : 1.6}
              fill={
                n >= 0 ? 'var(--ac)' : k.kind === 'virtual' ? 'transparent' : 'rgba(255,255,255,.9)'
              }
              stroke={isSel ? '#fff' : n >= 0 ? 'var(--ac)' : 'rgba(255,255,255,.9)'}
              strokeWidth={isSel ? 0.6 : 0.35}
              strokeDasharray={k.kind === 'virtual' && n < 0 ? '0.6 0.5' : undefined}
            />
            {n >= 0 && (
              <text
                x={k.x}
                y={k.y + 0.9}
                textAnchor="middle"
                fontSize={2.4}
                fontWeight={800}
                fill="#052412"
              >
                {n + 1}
              </text>
            )}
          </g>
        );
      })}
    </svg>
  );
}

/** "Preview pitch lines" (E05): the template projected onto the frame with the current points. */
function PitchOverlay({
  template,
  h,
  width,
  height,
}: {
  template: PitchTemplate;
  h: readonly number[];
  width: number;
  height: number;
}) {
  const paths = template.lines
    .filter((l) => l.points.length > 1)
    .map((l) => {
      const pts = (l.closed ? [...l.points, l.points[0]] : l.points)
        .map((p) => (p ? project(h, p) : null))
        .filter((p): p is Pt => p !== null);
      return { name: l.name, d: pts.map(([x, y]) => `${x.toFixed(1)},${y.toFixed(1)}`).join(' ') };
    });
  return (
    <svg
      aria-hidden="true"
      viewBox={`0 0 ${width} ${height}`}
      preserveAspectRatio="none"
      className="pointer-events-none absolute inset-0 h-full w-full"
      data-testid="pitch-preview"
    >
      <g fill="none" stroke="rgba(61,220,132,.9)" strokeWidth={2} strokeDasharray="8 6">
        {paths.map((p) => (
          <polyline key={p.name} points={p.d} />
        ))}
      </g>
    </svg>
  );
}
