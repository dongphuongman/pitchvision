import { Link, useNavigate } from '@tanstack/react-router';
import { Fragment, useState, type ReactNode } from 'react';
import {
  FilePen,
  MoreVertical,
  RotateCcw,
  SquareArrowOutUpRight,
  Trash2,
  Video,
} from 'lucide-react';
import { useTranslation } from 'react-i18next';
import { canRetry, type LiveJob } from '@/api/jobs';
import { isJobRunning, useJobProgress } from '@/api/useJobProgress';
import type { Video as VideoT } from '@/api/videos';
import { can } from '@/auth/roles';
import { useRole } from '@/auth/session';
import { useFormat } from '@/i18n/format';
import { cn } from '@/lib/cn';
import { useLongPress } from '@/lib/useLongPress';
import {
  Badge,
  DropdownMenu,
  DropdownMenuContent,
  DropdownMenuItem,
  DropdownMenuSeparator,
  DropdownMenuTrigger,
  IconButton,
  Progress,
  Tooltip,
  VIDEO_STATUS_TONE,
  type VideoStatus,
} from '@/ui';
import { cardStatus, cardTarget, hasJobs, videoTitle, type CardTarget } from './model';

export type VideoAction = 'rename' | 'retry' | 'delete';

export interface VideoCardProps {
  video: VideoT;
  view: 'grid' | 'list';
  /** `retry` acts on `video.latest_job`. */
  onAction: (action: VideoAction, video: VideoT) => void;
}

/** One library item (SCR-10 E06 card / list row, E07 ⋮ menu). */
export function VideoCard({ video, view, onAction }: VideoCardProps) {
  const { t } = useTranslation('library');
  const { t: tc } = useTranslation();
  const { t: te } = useTranslation('errors');
  const fmt = useFormat();
  const role = useRole();
  const navigate = useNavigate();
  // `latest_job` from the list, kept live over WS (status, progress, coverage, pitch quality).
  const { job } = useJobProgress(video, { enabled: hasJobs(video) });
  const status = cardStatus(video, job);
  const target = cardTarget(video.id, status);
  const title = videoTitle(video, t('card.untitled'));
  const live = isJobRunning(job);
  const pct = live && job ? Math.round(job.progress) : null;
  const coveragePct =
    (status === 'ready' || status === 'ready_degraded' || status === 'ready_no_pitch') &&
    job?.coverage
      ? fmt.percent(job.coverage.main_wide_pct)
      : null;

  const badgeLabel =
    status === 'analyzing' && pct !== null
      ? `${tc('status.video.analyzing')} ${fmt.percent(pct)}`
      : tc(`status.video.${status}`);
  const meta = metaLine(video, status, job, { t, te, fmt });

  const [menuOpen, setMenuOpen] = useState(false);
  const isEditor = can(role, 'editor');
  const items: { key: string; node: ReactNode }[] = [];
  if (target) {
    items.push({
      key: 'open',
      node: (
        <DropdownMenuItem
          icon={<SquareArrowOutUpRight size={15} />}
          onSelect={() => void navigate(targetHref(target))}
        >
          {t('menu.open')}
        </DropdownMenuItem>
      ),
    });
  }
  if (isEditor) {
    items.push({
      key: 'rename',
      node: (
        <DropdownMenuItem icon={<FilePen size={15} />} onSelect={() => onAction('rename', video)}>
          {t('menu.rename')}
        </DropdownMenuItem>
      ),
    });
    if (canRetry(job)) {
      items.push({
        key: 'retry',
        node: (
          <DropdownMenuItem
            icon={<RotateCcw size={15} />}
            onSelect={() => onAction('retry', video)}
          >
            {t('menu.retry')}
          </DropdownMenuItem>
        ),
      });
    }
    items.push({ key: 'sep', node: <DropdownMenuSeparator /> });
    items.push({
      key: 'delete',
      node: (
        <DropdownMenuItem
          danger
          icon={<Trash2 size={15} />}
          onSelect={() => onAction('delete', video)}
        >
          {t('menu.delete')}
        </DropdownMenuItem>
      ),
    });
  }

  const menu =
    items.length > 0 ? (
      <DropdownMenu modal={false} open={menuOpen} onOpenChange={setMenuOpen}>
        <DropdownMenuTrigger asChild>
          <IconButton
            label={t('card.menu', { title })}
            tooltip={false}
            icon={<MoreVertical size={17} />}
            className="relative z-[1] -mr-1.5 shrink-0"
          />
        </DropdownMenuTrigger>
        <DropdownMenuContent>
          {items.map((i) => (
            <Fragment key={i.key}>{i.node}</Fragment>
          ))}
        </DropdownMenuContent>
      </DropdownMenu>
    ) : null;

  const badge = (
    <Badge tone={VIDEO_STATUS_TONE[status]} size="sm" data-status={status}>
      {badgeLabel}
    </Badge>
  );
  // Prototype `.thumb .st .badge`: dark glass so the tone stays readable on any frame.
  const thumbBadge = (
    <Badge
      tone={VIDEO_STATUS_TONE[status]}
      size="sm"
      data-status={status}
      className={cn(
        'bg-[rgba(5,9,13,.72)] backdrop-blur-md',
        VIDEO_STATUS_TONE[status] === 'mute' && 'text-[#d6dde5]',
      )}
    >
      {badgeLabel}
    </Badge>
  );
  const coverage = coveragePct && (
    <Tooltip content={t('card.coverageHint', { pct: coveragePct })}>
      <span className="relative z-[1] rounded-full border border-line px-[7px] py-px text-[11px] font-semibold text-fg-2 tabular-nums">
        {t('card.coverage', { pct: coveragePct })}
      </span>
    </Tooltip>
  );
  const progress = live ? (
    <Progress
      value={pct}
      label={t('card.progress')}
      tone="info"
      size="xs"
      className="rounded-none bg-black/40"
    />
  ) : video.status === 'uploading' ? (
    <Progress
      value={null}
      label={t('card.uploadProgress')}
      tone="info"
      size="xs"
      className="rounded-none bg-black/40"
    />
  ) : null;

  // UIUX §09: press-and-hold (or right-click) anywhere on the card opens its ⋮ menu — a bottom sheet on phones
  const longPress = useLongPress(() => setMenuOpen(true));
  const pressable = menu ? longPress : {};
  const noCallout = menu && '[-webkit-touch-callout:none]';

  const heading = target ? (
    <TargetLink
      target={target}
      className="rounded-xs outline-none after:absolute after:inset-0 after:rounded-[inherit] focus-visible:after:ring-2 focus-visible:after:ring-accent"
    >
      {title}
    </TargetLink>
  ) : (
    <span>{title}</span>
  );

  if (view === 'list') {
    return (
      <article
        aria-label={title}
        className={cn(
          'group relative flex items-center gap-4 rounded-xl border border-line bg-surface-1 p-2.5 pr-3.5 transition-colors hover:border-line-strong',
          noCallout,
        )}
        {...pressable}
      >
        <Thumb video={video} className="w-[136px] shrink-0 rounded-lg" progress={progress} />
        <div className="min-w-0 flex-1">
          <h3 className="m-0 mb-1 truncate text-[14.5px] font-[650]">{heading}</h3>
          <p className="m-0 truncate text-[12.5px] text-fg-3">{meta}</p>
        </div>
        <div className="hidden shrink-0 items-center gap-2 sm:flex">
          {coverage}
          {badge}
        </div>
        {menu}
      </article>
    );
  }

  return (
    <article
      aria-label={title}
      className={cn(
        'group relative rounded-xl border border-line bg-surface-1 transition-[border-color,transform] duration-[120ms] hover:-translate-y-px hover:border-line-strong',
        noCallout,
      )}
      {...pressable}
    >
      <Thumb
        video={video}
        className="rounded-t-xl"
        progress={progress}
        badge={<span className="absolute top-2 left-2">{thumbBadge}</span>}
        duration={
          video.duration_ms && video.status !== 'uploading' ? (
            <span className="absolute right-2 bottom-2 rounded-[5px] bg-[rgba(5,9,13,.78)] px-1.5 py-0.5 font-mono text-[11.5px] font-semibold text-white">
              {fmt.timecode(video.duration_ms)}
            </span>
          ) : null
        }
      />
      <div className="flex gap-2 px-3.5 pt-3 pb-3.5">
        <div className="min-w-0 flex-1">
          <h3 className="m-0 mb-[3px] truncate text-[14.5px] font-[650]">{heading}</h3>
          <div className="flex min-w-0 items-center gap-2">
            <p className="m-0 min-w-0 truncate text-[12.5px] text-fg-3">{meta}</p>
            {coverage}
          </div>
        </div>
        {menu}
      </div>
    </article>
  );
}

function targetHref(target: NonNullable<CardTarget>) {
  return target.kind === 'workspace'
    ? ({
        to: '/videos/$videoId/$tab',
        params: { videoId: target.videoId, tab: 'overview' },
      } as const)
    : ({ to: '/videos/$videoId/processing', params: { videoId: target.videoId } } as const);
}

function TargetLink({
  target,
  className,
  children,
  ...rest
}: {
  target: NonNullable<CardTarget>;
  className?: string;
  children: ReactNode;
}) {
  return target.kind === 'workspace' ? (
    <Link
      to="/videos/$videoId/$tab"
      params={{ videoId: target.videoId, tab: 'overview' }}
      className={className}
      {...rest}
    >
      {children}
    </Link>
  ) : (
    <Link
      to="/videos/$videoId/processing"
      params={{ videoId: target.videoId }}
      className={className}
      {...rest}
    >
      {children}
    </Link>
  );
}

/** Thumbnail or a pitch-green placeholder (no thumbnails until the ingest stage stores one). */
function Thumb({
  video,
  className,
  progress,
  badge,
  duration,
}: {
  video: VideoT;
  className?: string;
  progress?: ReactNode;
  badge?: ReactNode;
  duration?: ReactNode;
}) {
  return (
    <div className={cn('relative aspect-video overflow-hidden bg-[#1d4f28]', className)}>
      {video.thumbnail_url ? (
        <img
          src={video.thumbnail_url}
          alt=""
          loading="lazy"
          className="absolute inset-0 size-full object-cover"
        />
      ) : (
        <div
          aria-hidden="true"
          data-testid="thumb-placeholder"
          className="absolute inset-0 grid place-items-center bg-[repeating-linear-gradient(90deg,#1d4f28_0_12.5%,#215a2e_12.5%_25%)]"
        >
          <span className="absolute inset-[14%_8%] rounded-[3px] border border-white/25" />
          <span className="absolute inset-y-[14%] left-1/2 border-l border-white/25" />
          <span className="absolute top-1/2 left-1/2 [aspect-ratio:1] size-[22%] -translate-1/2 rounded-full border border-white/25" />
          <Video size={26} className="relative text-white/55" />
        </div>
      )}
      {badge}
      {duration}
      {progress && <div className="absolute inset-x-0 bottom-0">{progress}</div>}
    </div>
  );
}

function metaLine(
  video: VideoT,
  status: VideoStatus,
  job: LiveJob | null,
  {
    t,
    te,
    fmt,
  }: {
    t: ReturnType<typeof useTranslation<'library'>>['t'];
    te: ReturnType<typeof useTranslation<'errors'>>['t'];
    fmt: ReturnType<typeof useFormat>;
  },
): string {
  const date = fmt.date(video.created_at, { month: 'short', day: 'numeric' });
  switch (status) {
    case 'uploading':
      return t('card.uploading');
    case 'ingesting':
      return job
        ? `${fmt.percent(Math.round(job.progress))} · ${t('card.preparing')}`
        : t('card.preparing');
    case 'queued':
    case 'analyzing': {
      if (!job || job.status === 'queued') return date;
      const parts = [fmt.percent(Math.round(job.progress))];
      const etaS = job.eta_s ?? 0;
      if (etaS > 0) {
        const eta = fmt.number(Math.max(1, Math.round(etaS / 60)), {
          style: 'unit',
          unit: 'minute',
          unitDisplay: 'narrow',
        });
        parts.push(t('card.eta', { time: eta }));
      }
      return parts.join(' · ');
    }
    case 'failed': {
      const code = job?.error?.code ?? video.error?.code;
      const reason = code ? te(`codes.${code}` as 'codes.UNKNOWN', { defaultValue: '' }) : '';
      return reason ? `${date} · ${reason}` : date;
    }
    case 'not_analyzed':
      return `${date} · ${t('card.notAnalyzed')}`;
    default:
      return video.duration_ms ? `${fmt.timecode(video.duration_ms)} · ${date}` : date;
  }
}
