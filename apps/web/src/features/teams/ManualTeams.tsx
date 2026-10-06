import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { getRouteApi } from '@tanstack/react-router';
import { ChevronLeft, ChevronRight, RefreshCw } from 'lucide-react';
import { useState } from 'react';
import { useTranslation } from 'react-i18next';
import {
  adoptJob,
  frameQuery,
  postManualTeams,
  teamCandidatesQuery,
  type CandidateFrame,
  type SeedBox,
} from '@/api/corrections';
import { isApiError } from '@/api/errors';
import { can } from '@/auth/roles';
import { useRole } from '@/auth/session';
import { cn } from '@/lib/cn';
import { useInView } from '@/lib/useInView';
import { Banner, Button, Card, IconButton, Skeleton, toast } from '@/ui';
import { CorrectionError } from '@/features/video/corrections';

const route = getRouteApi('/_app/videos/$videoId/teams');

/** Candidate boxes are in normalized-video pixels (1280 wide, AD §6). */
const VIDEO_W = 1280;
const MIN_PER_TEAM = 2;
const MAX_PER_TEAM = 20;
const TEAM_STYLE = [
  'border-[#e5484d] bg-[color-mix(in_srgb,#e5484d_22%,transparent)]',
  'border-[#3e8bff] bg-[color-mix(in_srgb,#3e8bff_22%,transparent)]',
] as const;

type TeamIdx = 0 | 1;
interface Pick extends SeedBox {
  team: TeamIdx;
}

const keyOf = (tMs: number, k: number) => `${tMs}:${k}`;

/** SCR-14 manual mode (E10–E13): pick ≥ 2 players per team on S3's sample frames. */
export function ManualTeams({ videoId }: { videoId: string }) {
  const { t } = useTranslation('video');
  const navigate = route.useNavigate();
  const qc = useQueryClient();
  const canEdit = can(useRole(), 'editor');
  const candidates = useQuery(teamCandidatesQuery(videoId));
  const [idx, setIdx] = useState(0);
  const [picks, setPicks] = useState<Record<string, Pick>>({});
  const [notice, setNotice] = useState('');

  const submit = useMutation({
    mutationFn: () => {
      const all = Object.values(picks);
      const seeds = (team: TeamIdx) =>
        all.filter((p) => p.team === team).map(({ t_ms, box }) => ({ t_ms, box }));
      return postManualTeams(videoId, { '0': seeds(0), '1': seeds(1) });
    },
    onSuccess: (job) => {
      adoptJob(qc, videoId, job);
      toast({ title: t('teams.manual.started'), tone: 'info' });
      void navigate({ to: '/videos/$videoId/processing', params: { videoId } });
    },
  });

  if (candidates.isPending) return <Skeleton className="h-96" />;
  if (candidates.isError) {
    if (isApiError(candidates.error) && candidates.error.status === 404) {
      return <Banner tone="info">{t('teams.manual.unavailable')}</Banner>;
    }
    return <CorrectionError error={candidates.error} videoId={videoId} />;
  }

  const frames = candidates.data.frames;
  const frame = frames[Math.min(idx, frames.length - 1)];
  if (!frame) return <Banner tone="info">{t('teams.manual.unavailable')}</Banner>;

  const teamName = (team: TeamIdx) => t('teams.group', { n: team + 1 });
  const all = Object.values(picks);
  const counts: [number, number] = [
    all.filter((p) => p.team === 0).length,
    all.filter((p) => p.team === 1).length,
  ];
  const ready = counts[0] >= MIN_PER_TEAM && counts[1] >= MIN_PER_TEAM;

  /** Assign (or clear with null) one candidate box; a full team is skipped / refused. */
  const assign = (k: number, team: TeamIdx | null) => {
    const key = keyOf(frame.t_ms, k);
    const box = frame.boxes[k];
    if (!box) return;
    if (team !== null && counts[team] >= MAX_PER_TEAM && picks[key]?.team !== team) {
      setNotice(t('teams.manual.full', { team: teamName(team) }));
      return;
    }
    const rest = Object.fromEntries(Object.entries(picks).filter(([k2]) => k2 !== key));
    setNotice('');
    setPicks(team === null ? rest : { ...rest, [key]: { t_ms: frame.t_ms, box, team } });
  };
  /** E11 click: none → Team 1 → Team 2 → none (full teams are skipped). */
  const cycle = (k: number) => {
    const current = picks[keyOf(frame.t_ms, k)]?.team;
    const order: (TeamIdx | null)[] =
      current === undefined ? [0, 1, null] : current === 0 ? [1, null] : [null];
    const target = order.find((o) => o === null || counts[o] < MAX_PER_TEAM) ?? null;
    assign(k, target);
  };

  return (
    <div className="flex flex-col gap-4">
      {canEdit ? (
        <Banner tone="info">
          {t('teams.manual.hint')} {t('teams.manual.keyboard')}
        </Banner>
      ) : (
        <Banner tone="info">{t('teams.readOnly')}</Banner>
      )}
      <div className="grid gap-3.5 lg:grid-cols-[minmax(0,1fr)_280px]">
        <div className="flex min-w-0 flex-col gap-2">
          <FrameStage
            videoId={videoId}
            frame={frame}
            n={idx + 1}
            picks={picks}
            disabled={!canEdit}
            onCycle={cycle}
            onAssign={assign}
          />
          <div className="flex items-center gap-2">
            <IconButton
              label={t('teams.manual.prev')}
              icon={<ChevronLeft size={16} />}
              disabled={idx === 0}
              onClick={() => setIdx(idx - 1)}
            />
            <span className="text-sm text-fg-2" aria-live="polite">
              {t('teams.manual.frameOf', { n: idx + 1, total: frames.length })}
            </span>
            <IconButton
              label={t('teams.manual.next')}
              icon={<ChevronRight size={16} />}
              disabled={idx >= frames.length - 1}
              onClick={() => setIdx(idx + 1)}
            />
          </div>
          <ul
            aria-label={t('teams.manual.frames')}
            className="m-0 flex list-none gap-1.5 overflow-x-auto p-0 pb-1"
          >
            {frames.map((f, i) => (
              <li key={f.t_ms}>
                <Thumb
                  videoId={videoId}
                  tMs={f.t_ms}
                  n={i + 1}
                  current={i === idx}
                  picked={f.boxes.filter((_, k) => picks[keyOf(f.t_ms, k)]).length}
                  onSelect={() => setIdx(i)}
                />
              </li>
            ))}
          </ul>
        </div>
        <Card className="flex flex-col gap-2 self-start">
          <h2 className="m-0 text-md font-bold">{t('teams.manual.picked')}</h2>
          <div aria-live="polite" className="flex flex-col gap-1.5">
            {([0, 1] as const).map((team) => (
              <p key={team} className="m-0 flex items-center gap-2 text-sm">
                <span
                  aria-hidden="true"
                  className={cn('h-3 w-3 rounded-full border-2', TEAM_STYLE[team])}
                />
                {t('teams.manual.count', { team: teamName(team), count: counts[team] })}
              </p>
            ))}
            {notice && <p className="m-0 text-caption text-warn">{notice}</p>}
          </div>
          <hr className="my-1.5 border-line" />
          <p className="m-0 text-caption text-fg-3">
            {ready ? t('teams.manual.note') : t('teams.manual.need')}
          </p>
        </Card>
      </div>
      {submit.isError && <CorrectionError error={submit.error} videoId={videoId} />}
      <div className="flex justify-end gap-2">
        <Button
          variant="ghost"
          onClick={() =>
            void navigate({ search: (s) => ({ ...s, mode: undefined }), replace: true })
          }
        >
          {t('corrections.cancel')}
        </Button>
        {canEdit && (
          <Button
            variant="primary"
            disabled={!ready || submit.isPending}
            onClick={() => submit.mutate()}
          >
            <RefreshCw size={16} aria-hidden="true" />
            {t('teams.manual.submit')}
          </Button>
        )}
      </div>
    </div>
  );
}

function FrameStage({
  videoId,
  frame,
  n,
  picks,
  disabled,
  onCycle,
  onAssign,
}: {
  videoId: string;
  frame: CandidateFrame;
  n: number;
  picks: Record<string, Pick>;
  disabled: boolean;
  onCycle: (k: number) => void;
  onAssign: (k: number, team: TeamIdx | null) => void;
}) {
  const { t } = useTranslation('video');
  const image = useQuery(frameQuery(videoId, frame.t_ms));
  // Frame height in video pixels, from the still's aspect ratio (16:9 until it has loaded).
  const [videoH, setVideoH] = useState((VIDEO_W * 9) / 16);

  return (
    <div
      role="group"
      aria-label={t('teams.manual.stage', { n, count: frame.boxes.length })}
      className="relative w-full overflow-hidden rounded-xl bg-black"
      style={{ aspectRatio: `${VIDEO_W} / ${videoH}` }}
    >
      {image.data ? (
        <img
          src={image.data.url}
          alt=""
          className="absolute inset-0 h-full w-full select-none"
          draggable={false}
          onLoad={(e) => {
            const { naturalWidth: w, naturalHeight: h } = e.currentTarget;
            if (w > 0 && h > 0) setVideoH((VIDEO_W * h) / w);
          }}
        />
      ) : image.isError ? (
        <p className="absolute inset-0 m-0 grid place-items-center text-sm text-white">
          {t('teams.manual.frameError')}
        </p>
      ) : (
        <Skeleton className="absolute inset-0 h-full w-full" />
      )}
      {frame.boxes.map(([x1, y1, x2, y2], k) => {
        const team = picks[keyOf(frame.t_ms, k)]?.team;
        return (
          <button
            key={k}
            type="button"
            disabled={disabled}
            aria-label={
              team === undefined
                ? t('teams.manual.box', { n: k + 1 })
                : t('teams.manual.boxTeam', {
                    n: k + 1,
                    team: t('teams.group', { n: team + 1 }),
                  })
            }
            onClick={() => onCycle(k)}
            onKeyDown={(e) => {
              const team: TeamIdx | null | undefined =
                e.key === '1'
                  ? 0
                  : e.key === '2'
                    ? 1
                    : e.key === '0' || e.key === 'Delete' || e.key === 'Backspace'
                      ? null
                      : undefined;
              if (team === undefined) return;
              e.preventDefault();
              onAssign(k, team);
            }}
            className={cn(
              'absolute rounded-xs border-2 focus-ring disabled:cursor-default',
              team === undefined
                ? 'border-white/70 bg-transparent hover:bg-white/15'
                : TEAM_STYLE[team],
            )}
            style={{
              left: `${(x1 / VIDEO_W) * 100}%`,
              top: `${(y1 / videoH) * 100}%`,
              width: `${((x2 - x1) / VIDEO_W) * 100}%`,
              height: `${((y2 - y1) / videoH) * 100}%`,
            }}
          >
            {team !== undefined && (
              <span
                aria-hidden="true"
                className="absolute -top-5 left-0 rounded-xs bg-black/70 px-1 text-[11px] font-bold text-white"
              >
                {team + 1}
              </span>
            )}
          </button>
        );
      })}
    </div>
  );
}

/** Sample frame thumbnail: the still is fetched once it scrolls into view (or is selected). */
function Thumb({
  videoId,
  tMs,
  n,
  current,
  picked,
  onSelect,
}: {
  videoId: string;
  tMs: number;
  n: number;
  current: boolean;
  picked: number;
  onSelect: () => void;
}) {
  const { t } = useTranslation('video');
  const [el, setEl] = useState<HTMLButtonElement | null>(null);
  const inView = useInView(el);
  const image = useQuery(frameQuery(videoId, tMs, { enabled: inView || current }));
  return (
    <button
      ref={setEl}
      type="button"
      aria-label={t('teams.manual.thumb', { n })}
      aria-current={current ? 'true' : undefined}
      onClick={onSelect}
      className={cn(
        'relative block h-[45px] w-20 overflow-hidden rounded-md border-2 bg-surface-3 focus-ring',
        current ? 'border-accent' : 'border-transparent',
      )}
    >
      {image.data && <img src={image.data.url} alt="" className="h-full w-full object-cover" />}
      {picked > 0 && (
        <span className="absolute right-0.5 bottom-0.5 rounded-xs bg-black/70 px-1 text-[10px] font-bold text-white">
          {picked}
        </span>
      )}
    </button>
  );
}
