import { SkipForward } from 'lucide-react';
import { useMemo, useRef, type KeyboardEvent, type PointerEvent } from 'react';
import { useTranslation } from 'react-i18next';
import type { Shot } from '@/api/types';
import { formatTimecode } from '@/i18n/format';
import { cn } from '@/lib/cn';
import { Button } from '@/ui';
import { nextLiveActionMs, shotAt, timelineSegments } from './shots';

export interface ShotTimelineProps {
  shots: readonly Shot[];
  durationMs: number;
  currentMs: number;
  onSeek: (ms: number) => void;
  className?: string;
}

const SEGMENT_CLASS: Record<Shot['type'], string> = {
  main_wide: 'bg-[color-mix(in_srgb,var(--ac)_55%,transparent)]',
  replay: 'bg-[repeating-linear-gradient(135deg,#5b6672_0_6px,#4b5560_6px_12px)]',
  close_up: 'bg-[#4b5560]',
  other: 'bg-[#3a424b]',
};

const KEY_STEP_MS = 5_000;

/**
 * Shot timeline (UIUX SCR-20 v1.1): the bar is coloured by shot type — live wide play in the
 * accent colour, replay / close-up / other in grey with a label — click or arrow keys to seek,
 * plus "Skip to next live action" while a non-live shot is playing.
 */
export function ShotTimeline({
  shots,
  durationMs,
  currentMs,
  onSeek,
  className,
}: ShotTimelineProps) {
  const { t } = useTranslation('video');
  const { t: tc } = useTranslation();
  const bar = useRef<HTMLDivElement>(null);
  const segments = useMemo(() => timelineSegments(shots, durationMs), [shots, durationMs]);
  const total = durationMs > 0 ? durationMs : (shots.at(-1)?.t1_ms ?? 0);
  const current = shotAt(shots, currentMs);
  const nextLive = nextLiveActionMs(shots, currentMs);
  const playhead = total > 0 ? Math.min(100, Math.max(0, (currentMs / total) * 100)) : 0;

  const seekToClientX = (clientX: number) => {
    const el = bar.current;
    if (!el || total <= 0) return;
    const r = el.getBoundingClientRect();
    if (r.width <= 0) return;
    const ratio = Math.min(1, Math.max(0, (clientX - r.left) / r.width));
    onSeek(Math.round(ratio * total));
  };

  const onPointerDown = (e: PointerEvent<HTMLDivElement>) => {
    if (e.button !== 0) return;
    seekToClientX(e.clientX);
  };

  const onKeyDown = (e: KeyboardEvent<HTMLDivElement>) => {
    const step = e.shiftKey ? KEY_STEP_MS * 6 : KEY_STEP_MS;
    const next =
      e.key === 'ArrowRight'
        ? currentMs + step
        : e.key === 'ArrowLeft'
          ? currentMs - step
          : e.key === 'Home'
            ? 0
            : e.key === 'End'
              ? total
              : null;
    if (next === null) return;
    e.preventDefault();
    onSeek(Math.min(total, Math.max(0, next)));
  };

  return (
    <div className={cn('flex flex-col gap-2', className)}>
      <div
        ref={bar}
        role="slider"
        tabIndex={0}
        aria-label={t('player.timeline')}
        aria-valuemin={0}
        aria-valuemax={Math.round(total / 1000)}
        aria-valuenow={Math.round(currentMs / 1000)}
        aria-valuetext={`${formatTimecode(currentMs)} · ${current ? tc(`status.shot.${current.type}`) : ''}`}
        onPointerDown={onPointerDown}
        onKeyDown={onKeyDown}
        className="relative h-7 cursor-pointer overflow-hidden rounded-md bg-surface-3 outline-none select-none focus-visible:ring-2 focus-visible:ring-accent"
      >
        {segments.map((s) => (
          <div
            key={s.t0}
            data-testid="shot-segment"
            data-type={s.type}
            title={`${tc(`status.shot.${s.type}`)} · ${formatTimecode(s.t0)}–${formatTimecode(s.t1)}`}
            className={cn(
              'absolute inset-y-0 flex items-center overflow-hidden border-r border-black/25',
              SEGMENT_CLASS[s.type],
            )}
            style={{ left: `${s.left}%`, width: `${s.width}%` }}
          >
            {s.type !== 'main_wide' && s.width >= 4 && (
              <span className="truncate px-1.5 text-[10.5px] font-semibold text-white/85">
                {tc(`status.shot.${s.type}`)}
              </span>
            )}
          </div>
        ))}
        <div
          aria-hidden="true"
          data-testid="playhead"
          className="pointer-events-none absolute inset-y-0 w-0.5 -translate-x-1/2 bg-white shadow-[0_0_0_1px_rgba(0,0,0,.5)]"
          style={{ left: `${playhead}%` }}
        />
      </div>
      <div className="flex min-h-[30px] items-center gap-3 text-caption text-fg-3">
        <span className="font-mono tabular-nums">
          {formatTimecode(currentMs)} / {formatTimecode(total)}
        </span>
        {current && current.type !== 'main_wide' && (
          <span>{t('player.notLive', { shot: tc(`status.shot.${current.type}`) })}</span>
        )}
        <div className="flex-1" />
        {nextLive !== null && (
          <Button size="sm" onClick={() => onSeek(nextLive)}>
            <SkipForward size={14} aria-hidden="true" />
            {t('player.skipToLive')}
          </Button>
        )}
      </div>
    </div>
  );
}
