import { useQuery } from '@tanstack/react-query';
import { Download } from 'lucide-react';
import { useEffect, useMemo, useState } from 'react';
import { useTranslation } from 'react-i18next';
import { heatmapQuery, statsQuery, type HeatmapFilter } from '@/api/stats';
import type { TeamInfo } from '@/api/types';
import { useFormat } from '@/i18n/format';
import { renderHeatmap, type AveragePosition } from '@/player/heatmap';
import { Button, Card, Checkbox, Select, Skeleton } from '@/ui';

const ALL = 'all';

export interface HeatmapsTabProps {
  videoId: string;
  revision: number;
  teams: readonly TeamInfo[];
  /** URL state (SCR-20c E01–E05: shareable). */
  team: number | undefined;
  track: number | undefined;
  avg: boolean;
  onChange: (next: { team?: number; track?: number; avg?: 1 }) => void;
  title: string;
}

/**
 * SCR-20c Heatmaps (M3, FR-503 / FR-504): where a team or one track was, from the 5 Hz positions, smoothed on
 * the client; average positions with their spread on top. Phase (in / out of possession) and Compare wait for
 * possession (M3-CV-02); "use timeline selection" for the timeline range selection.
 */
export function HeatmapsTab({
  videoId,
  revision,
  teams,
  team,
  track,
  avg,
  onChange,
  title,
}: HeatmapsTabProps) {
  const { t } = useTranslation('video');
  const fmt = useFormat();
  const [canvas, setCanvas] = useState<HTMLCanvasElement | null>(null);
  const stats = useQuery(statsQuery(videoId, revision));
  const filter: HeatmapFilter = {
    ...(team !== undefined ? { cluster: team } : {}),
    ...(track !== undefined ? { track_id: track } : {}),
  };
  const heat = useQuery(heatmapQuery(videoId, filter, revision));
  const color = (cluster: number | null | undefined) =>
    teams.find((x) => x.cluster === cluster)?.color ?? '#e6edf3';

  const tracks = useMemo(
    () =>
      (stats.data?.players ?? []).filter(
        (p) => p.role !== 'referee' && (team === undefined || p.cluster === team),
      ),
    [stats.data, team],
  );
  const avgDots: AveragePosition[] = useMemo(
    () =>
      avg
        ? tracks
            .filter((p) => track === undefined || p.track_id === track)
            .flatMap((p) =>
              p.avg_x != null && p.avg_y != null
                ? [
                    {
                      x: p.avg_x,
                      y: p.avg_y,
                      cov: p.cov ?? null,
                      color: color(p.cluster),
                      label: `#${p.track_id}`,
                    },
                  ]
                : [],
            )
        : [],
    [avg, tracks, track], // eslint-disable-line react-hooks/exhaustive-deps
  );

  useEffect(() => {
    if (!canvas) return;
    const paint = () => renderHeatmap(canvas, heat.data ?? null, avgDots);
    paint();
    if (typeof ResizeObserver === 'undefined') return;
    const ro = new ResizeObserver(paint);
    ro.observe(canvas);
    return () => ro.disconnect();
  }, [canvas, heat.data, avgDots]);

  const download = () => {
    if (!canvas) return;
    const a = document.createElement('a');
    a.href = canvas.toDataURL('image/png');
    a.download = `${title} heatmap.png`.replace(/[\\/:*?"<>|]+/g, ' ');
    a.click();
  };

  const teamOptions = [
    { value: ALL, label: t('heatmaps.bothTeams') },
    ...teams.map((x) => ({ value: String(x.cluster), label: x.name })),
  ];
  const trackOptions = [
    { value: ALL, label: t('heatmaps.allTracks') },
    ...tracks.map((p) => ({
      value: String(p.track_id),
      label: t('heatmaps.trackOption', {
        track: p.track_id,
        seen: fmt.timecode(p.observed_s * 1000),
      }),
    })),
  ];
  const seconds = heat.data ? heat.data.total * heat.data.sample_s : 0;

  return (
    <Card className="grid gap-4">
      <div className="flex flex-wrap items-end gap-3">
        <label className="grid gap-1 text-caption text-fg-2">
          {t('heatmaps.team')}
          <Select
            size="sm"
            aria-label={t('heatmaps.team')}
            options={teamOptions}
            value={team === undefined ? ALL : String(team)}
            onValueChange={(v) =>
              onChange({ team: v === ALL ? undefined : Number(v), track: undefined })
            }
          />
        </label>
        <label className="grid gap-1 text-caption text-fg-2">
          {t('heatmaps.track')}
          <Select
            size="sm"
            aria-label={t('heatmaps.track')}
            options={trackOptions}
            value={track === undefined ? ALL : String(track)}
            onValueChange={(v) => onChange({ team, track: v === ALL ? undefined : Number(v) })}
          />
        </label>
        <Checkbox
          label={t('heatmaps.averagePositions')}
          checked={avg}
          onCheckedChange={(c) => onChange({ team, track, avg: c === true ? 1 : undefined })}
        />
        <Button
          size="sm"
          variant="secondary"
          className="ml-auto"
          onClick={download}
          disabled={!heat.data}
        >
          <Download size={14} aria-hidden="true" />
          {t('heatmaps.download')}
        </Button>
      </div>
      {heat.isPending ? (
        <Skeleton className="aspect-[111/74] w-full" />
      ) : (
        <canvas
          ref={setCanvas}
          className="aspect-[111/74] w-full rounded-lg"
          role="img"
          aria-label={t('heatmaps.canvasLabel', {
            who:
              track !== undefined
                ? t('heatmaps.trackName', { track })
                : team !== undefined
                  ? (teams.find((x) => x.cluster === team)?.name ?? '')
                  : t('heatmaps.bothTeams'),
          })}
          data-testid="heatmap"
        />
      )}
      <p className="m-0 text-caption text-fg-3">
        {heat.data && heat.data.total === 0
          ? t('heatmaps.empty')
          : t('heatmaps.note', { time: fmt.timecode(seconds * 1000) })}
      </p>
    </Card>
  );
}
