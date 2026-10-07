import { useQuery } from '@tanstack/react-query';
import { Check, Play, X } from 'lucide-react';
import { useEffect, useMemo, useState } from 'react';
import { useTranslation } from 'react-i18next';
import { passesQuery } from '@/api/stats';
import type { TeamInfo } from '@/api/types';
import { useFormat } from '@/i18n/format';
import { renderPassMap } from '@/player/passMap';
import { Card, Checkbox, IconButton, Select, Skeleton } from '@/ui';

export interface PassingTabProps {
  videoId: string;
  revision: number;
  teams: readonly TeamInfo[];
  team: number | undefined;
  onTeam: (team: number) => void;
  onSeek: (ms: number) => void;
}

/**
 * SCR-20d Passing (M3-CV-02, FR-505): passes inferred from possession as arrows on the pitch + a list; ▶ watches
 * one from a second before (E05). The pass NETWORK between players (E03/E04) needs player identities across shots
 * (ReID, M2) — tracks are per shot until then, so links between them would be meaningless.
 */
export function PassingTab({ videoId, revision, teams, team, onTeam, onSeek }: PassingTabProps) {
  const { t } = useTranslation('video');
  const fmt = useFormat();
  const cluster = team ?? teams[0]?.cluster ?? 0;
  const [showLost, setShowLost] = useState(true);
  const [hover, setHover] = useState<number | null>(null);
  const [canvas, setCanvas] = useState<HTMLCanvasElement | null>(null);
  const q = useQuery(passesQuery(videoId, cluster, revision));
  const color = teams.find((x) => x.cluster === cluster)?.color ?? '#e6edf3';
  const shown = useMemo(
    () => (q.data?.items ?? []).filter((p) => showLost || p.completed),
    [q.data, showLost],
  );
  const completed = shown.filter((p) => p.completed).length;

  useEffect(() => {
    if (!canvas) return;
    const paint = () => renderPassMap(canvas, shown, { color, highlight: hover });
    paint();
    if (typeof ResizeObserver === 'undefined') return;
    const ro = new ResizeObserver(paint);
    ro.observe(canvas);
    return () => ro.disconnect();
  }, [canvas, shown, color, hover]);

  return (
    <Card className="grid gap-4">
      <div className="flex flex-wrap items-end gap-3">
        <label className="grid gap-1 text-caption text-fg-2">
          {t('passing.team')}
          <Select
            size="sm"
            aria-label={t('passing.team')}
            options={teams.map((x) => ({ value: String(x.cluster), label: x.name }))}
            value={String(cluster)}
            onValueChange={(v) => onTeam(Number(v))}
          />
        </label>
        <Checkbox
          label={t('passing.showLost')}
          checked={showLost}
          onCheckedChange={(c) => setShowLost(c === true)}
        />
        <p className="text-body-sm m-0 ml-auto text-fg-2">
          {t('passing.summary', { count: shown.length, completed })}
        </p>
      </div>
      <div className="grid gap-4 lg:grid-cols-[minmax(0,1fr)_320px]">
        {q.isPending ? (
          <Skeleton className="aspect-[111/74] w-full" />
        ) : (
          <canvas
            ref={setCanvas}
            className="aspect-[111/74] w-full rounded-lg"
            role="img"
            aria-label={t('passing.mapLabel', { count: shown.length })}
            data-testid="pass-map"
          />
        )}
        <ol
          className="m-0 grid max-h-[420px] list-none content-start gap-0.5 overflow-auto p-0"
          aria-label={t('passing.list')}
        >
          {shown.length === 0 && !q.isPending && (
            <li className="text-body-sm text-fg-3">{t('passing.empty')}</li>
          )}
          {shown.map((p, i) => (
            <li
              key={`${p.t_ms}-${p.from_track}`}
              className="text-body-sm flex items-center gap-2 rounded-md px-1.5 py-1 hover:bg-surface-2"
              onMouseEnter={() => setHover(i)}
              onMouseLeave={() => setHover(null)}
            >
              <span className="w-12 font-mono text-caption text-fg-3 tabular-nums">
                {fmt.timecode(p.t_ms)}
              </span>
              {p.completed ? (
                <Check size={14} className="text-accent" aria-label={t('passing.completed')} />
              ) : (
                <X size={14} className="text-danger" aria-label={t('passing.lost')} />
              )}
              <span className="flex-1 truncate">
                {t('passing.fromTo', { from: p.from_track, to: p.to_track })}
              </span>
              <IconButton
                size="sm"
                label={t('passing.watch', { time: fmt.timecode(p.t_ms) })}
                icon={<Play size={13} />}
                onClick={() => onSeek(Math.max(0, p.t_ms - 1000))}
              />
            </li>
          ))}
        </ol>
      </div>
      <p className="m-0 text-caption text-fg-3">{t('passing.note')}</p>
    </Card>
  );
}
