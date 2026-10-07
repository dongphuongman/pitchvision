import { useQuery } from '@tanstack/react-query';
import { useTranslation } from 'react-i18next';
import { statsQuery } from '@/api/stats';
import type { Stats, TeamInfo, TeamStats } from '@/api/types';
import { useFormat } from '@/i18n/format';
import { Badge, Skeleton, Tooltip } from '@/ui';

const ZONES = ['walk', 'jog', 'run', 'sprint'] as const;
const ZONE_SHADE = { walk: 0.25, jog: 0.45, run: 0.7, sprint: 1 } as const;

export interface StatsPanelProps {
  videoId: string;
  revision: number;
  teams: readonly TeamInfo[];
  /** Video time on the main camera (manifest coverage) — the badge (M3: "Tactical data for 71 %"). */
  mainWidePct: number;
  onSeek: (ms: number) => void;
}

/**
 * SCR-20a Overview stats (M3): distance, top speed, sprints and speed zones per team, all over the OBSERVED time
 * (badge), with the full-match extrapolation labelled as an estimate. A top speed jumps to its moment (UIUX §2.1).
 */
export function StatsPanel({ videoId, revision, teams, mainWidePct, onSeek }: StatsPanelProps) {
  const { t } = useTranslation('video');
  const fmt = useFormat();
  const q = useQuery(statsQuery(videoId, revision));
  if (q.isPending) return <Skeleton className="h-40" />;
  if (q.isError) return <p className="text-body-sm m-0 text-fg-2">{t('stats.error')}</p>;
  const s = q.data;
  if (!s.available) return <p className="text-body-sm m-0 text-fg-2">{t('stats.unavailable')}</p>;

  const byCluster = new Map(s.teams.map((x) => [x.cluster, x]));
  const hasPossession = s.teams.some((x) => x.possession_pct != null);
  const cols = teams.map((team) => ({ team, st: byCluster.get(team.cluster) }));
  const km = (m: number) =>
    fmt.number(m / 1000, { maximumFractionDigits: 1, minimumFractionDigits: 1 });
  const row = (label: string, cell: (st: TeamStats) => React.ReactNode, hint?: string) => (
    <tr className="border-t border-line">
      <th scope="row" className="py-2 pr-3 text-left font-medium text-fg-2">
        {hint ? (
          <Tooltip content={hint}>
            <span className="cursor-help underline decoration-dotted underline-offset-4">
              {label}
            </span>
          </Tooltip>
        ) : (
          label
        )}
      </th>
      {cols.map(({ team, st }) => (
        <td key={team.cluster} className="py-2 pr-3 tabular-nums">
          {st ? cell(st) : '—'}
        </td>
      ))}
    </tr>
  );

  return (
    <section aria-label={t('stats.title')} className="grid gap-3">
      <div className="flex flex-wrap items-center gap-2">
        <h2 className="m-0 text-caption font-semibold tracking-wide text-fg-2 uppercase">
          {t('stats.title')}
        </h2>
        <Badge tone="run" size="sm">
          {t('stats.coverage', { pct: Math.round(mainWidePct) })}
        </Badge>
      </div>
      <div className="overflow-x-auto">
        <table className="text-body-sm w-full min-w-[420px] border-collapse">
          <thead>
            <tr>
              <th scope="col" className="sr-only">
                {t('stats.metric')}
              </th>
              {cols.map(({ team }) => (
                <th key={team.cluster} scope="col" className="pb-2 text-left">
                  <span className="inline-flex items-center gap-2">
                    <span
                      aria-hidden="true"
                      className="h-3 w-3 rounded-full"
                      style={{ background: team.color }}
                    />
                    {team.name}
                  </span>
                </th>
              ))}
            </tr>
          </thead>
          <tbody>
            {hasPossession &&
              row(
                t('stats.possession'),
                (st) =>
                  st.possession_pct != null ? (
                    <PossessionCell st={st} color={teamColor(teams, st)} />
                  ) : (
                    '—'
                  ),
                t('stats.possessionHint'),
              )}
            {hasPossession &&
              row(
                t('stats.passes'),
                (st) =>
                  st.passes != null && st.passes > 0
                    ? t('stats.passesValue', {
                        count: st.passes,
                        pct: fmt.number((100 * (st.passes_completed ?? 0)) / st.passes, {
                          maximumFractionDigits: 0,
                        }),
                        lost: st.balls_lost ?? 0,
                      })
                    : '—',
                t('stats.passesHint'),
              )}
            {row(
              t('stats.distance'),
              (st) => (
                <div className="grid gap-0.5">
                  <span>{t('stats.km', { value: km(st.distance_m) })}</span>
                  {st.estimated_distance_m != null && (
                    <span className="inline-flex flex-wrap items-center gap-1.5 text-caption text-fg-3">
                      {t('stats.matchEstimate', { value: km(st.estimated_distance_m) })}
                      <Badge tone="mute" size="sm">
                        {t('stats.estimated')}
                      </Badge>
                    </span>
                  )}
                </div>
              ),
              t('stats.distanceHint'),
            )}
            {row(
              t('stats.intensity'),
              (st) =>
                st.m_per_min != null
                  ? t('stats.perMin', {
                      value: fmt.number(st.m_per_min, { maximumFractionDigits: 0 }),
                    })
                  : '—',
              t('stats.intensityHint'),
            )}
            {row(t('stats.topSpeed'), (st) =>
              st.top_speed ? (
                <button
                  type="button"
                  className="text-left text-accent underline-offset-4 hover:underline"
                  onClick={() => st.top_speed && onSeek(st.top_speed.at_ms)}
                >
                  {t('stats.kmh', {
                    value: fmt.number(st.top_speed.kmh, { maximumFractionDigits: 1 }),
                  })}{' '}
                  <span className="text-fg-3">
                    {t('stats.trackAt', {
                      track: st.top_speed.track_id,
                      time: fmt.timecode(st.top_speed.at_ms),
                    })}
                  </span>
                </button>
              ) : (
                '—'
              ),
            )}
            {row(t('stats.sprints'), (st) => fmt.number(st.sprints), t('stats.sprintsHint'))}
            {row(
              t('stats.zones'),
              (st) => (
                <ZoneBar st={st} color={teamColor(teams, st)} />
              ),
              t('stats.zonesHint'),
            )}
            {row(t('stats.observed'), (st) =>
              t('stats.playerMin', {
                value: fmt.number(st.observed_player_s / 60, { maximumFractionDigits: 1 }),
                tracks: st.tracks,
              }),
            )}
          </tbody>
        </table>
      </div>
      {(s.possession_timeline ?? []).length > 0 && (
        <PossessionTimeline
          minutes={s.possession_timeline ?? []}
          teams={teams}
          onSeek={onSeek}
          label={t('stats.possessionOverTime')}
        />
      )}
      <p className="m-0 text-caption text-fg-3">{t('stats.note')}</p>
    </section>
  );
}

function teamColor(teams: readonly TeamInfo[], st: TeamStats): string {
  return teams.find((x) => x.cluster === st.cluster)?.color ?? 'var(--ac)';
}

function ZoneBar({ st, color }: { st: TeamStats; color: string }) {
  const { t } = useTranslation('video');
  const fmt = useFormat();
  const total = ZONES.reduce((a, z) => a + st.zones_m[z], 0);
  if (total <= 0) return <>—</>;
  const parts = ZONES.map((z) => ({ z, pct: (100 * st.zones_m[z]) / total }));
  return (
    <div className="grid gap-1">
      <div
        className="flex h-2.5 w-full max-w-[220px] overflow-hidden rounded-full bg-surface-3"
        role="img"
        aria-label={parts
          .map(
            (p) => `${t(`stats.zone.${p.z}`)} ${fmt.number(p.pct, { maximumFractionDigits: 0 })}%`,
          )
          .join(', ')}
      >
        {parts.map((p) => (
          <span
            key={p.z}
            style={{ width: `${p.pct}%`, background: color, opacity: ZONE_SHADE[p.z] }}
            className="h-full"
          />
        ))}
      </div>
      <span className="text-caption text-fg-3">
        {t('stats.sprintShare', {
          pct: fmt.number(parts[3]?.pct ?? 0, { maximumFractionDigits: 0 }),
        })}
      </span>
    </div>
  );
}

function PossessionCell({ st, color }: { st: TeamStats; color: string }) {
  const fmt = useFormat();
  const pct = st.possession_pct ?? 0;
  return (
    <div className="grid max-w-[220px] gap-1">
      <span>{fmt.number(pct, { maximumFractionDigits: 0 })}%</span>
      <span className="h-1.5 w-full overflow-hidden rounded-full bg-surface-3" aria-hidden="true">
        <span className="block h-full" style={{ width: `${pct}%`, background: color }} />
      </span>
    </div>
  );
}

/** SCR-20a E03: one bar per video minute, split by team; click a minute to watch it. */
function PossessionTimeline({
  minutes,
  teams,
  onSeek,
  label,
}: {
  minutes: NonNullable<Stats['possession_timeline']>;
  teams: readonly TeamInfo[];
  onSeek: (ms: number) => void;
  label: string;
}) {
  const { t } = useTranslation('video');
  const fmt = useFormat();
  const [a, b] = teams;
  return (
    <figure className="m-0 grid gap-1.5">
      <figcaption className="text-caption text-fg-2">{label}</figcaption>
      <div className="flex h-14 items-stretch gap-[2px]" role="list">
        {minutes.map((m) => {
          const ma = a ? (m.ms[String(a.cluster)] ?? 0) : 0;
          const mb = b ? (m.ms[String(b.cluster)] ?? 0) : 0;
          const tot = ma + mb;
          const share = tot > 0 ? ma / tot : 0.5;
          const name = t('stats.minuteLabel', {
            minute: fmt.timecode(m.minute * 60_000),
            a: a?.name ?? '',
            pa: fmt.number(100 * share, { maximumFractionDigits: 0 }),
            b: b?.name ?? '',
            pb: fmt.number(100 * (1 - share), { maximumFractionDigits: 0 }),
          });
          return (
            <button
              key={m.minute}
              type="button"
              role="listitem"
              aria-label={name}
              title={name}
              onClick={() => onSeek(m.minute * 60_000)}
              className="flex min-w-[6px] flex-1 flex-col overflow-hidden rounded-[3px] opacity-90 hover:opacity-100 focus-visible:outline-2 focus-visible:outline-accent"
            >
              <span style={{ flexGrow: share, background: a?.color }} />
              <span style={{ flexGrow: 1 - share, background: b?.color }} />
            </button>
          );
        })}
      </div>
    </figure>
  );
}
