import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { ChevronDown } from 'lucide-react';
import { useTranslation } from 'react-i18next';
import { keyRolesQuery, patchPlayers, type PlayerFix, type PlayerOut } from '@/api/corrections';
import { formatTimecode } from '@/i18n/format';
import {
  DropdownMenu,
  DropdownMenuContent,
  DropdownMenuItem,
  DropdownMenuTrigger,
  Skeleton,
  toast,
} from '@/ui';

const SHOWN = 8;
const REFEREE_COLOR = '#f5d90a';

interface TeamLabel {
  cluster: number;
  name: string;
  color: string;
}

/**
 * SCR-14 E06 "Referees & goalkeepers": the longest goalkeeper / referee tracks with their team;
 * editors fix a wrong one from its menu (saved at once, display-only — the manifest's
 * track_overrides recolour the player and exports).
 */
export function KeyRoles({
  videoId,
  teams,
  readOnly,
}: {
  videoId: string;
  teams: readonly TeamLabel[];
  readOnly: boolean;
}) {
  const { t } = useTranslation('video');
  const qc = useQueryClient();
  const q = useQuery(keyRolesQuery(videoId));
  const fix = useMutation({
    mutationFn: (change: PlayerFix) => patchPlayers(videoId, [change]),
    onSuccess: (data) => {
      qc.setQueryData(keyRolesQuery(videoId).queryKey, data);
      void qc.invalidateQueries({ queryKey: ['results', videoId, 'manifest'] });
      toast({ title: t('teams.fixed'), tone: 'success' });
    },
  });

  const teamOf = (p: PlayerOut) => teams.find((x) => x.cluster === p.team);
  const who = (p: PlayerOut) =>
    p.role === 'referee'
      ? t('teams.ref', { id: p.track_id })
      : `${t('teams.gk', { id: p.track_id })} · ${teamOf(p)?.name ?? '—'}`;

  return (
    <section aria-labelledby="key-roles" className="flex flex-col gap-2">
      <h2 id="key-roles" className="m-0 text-md font-bold">
        {t('teams.keyRoles')}
      </h2>
      <p className="m-0 text-caption text-fg-3">{t('teams.keyRolesHelp')}</p>
      {q.isPending ? (
        <Skeleton className="h-9" />
      ) : !q.data?.items.length ? (
        <p className="m-0 text-control text-fg-3">{t('teams.noKeyRoles')}</p>
      ) : (
        <ul className="m-0 flex list-none flex-wrap gap-2 p-0">
          {q.data.items.slice(0, SHOWN).map((p) => {
            const color = p.role === 'referee' ? REFEREE_COLOR : (teamOf(p)?.color ?? '#9aa4b2');
            const chip = (
              <>
                <span
                  aria-hidden="true"
                  className="h-2.5 w-2.5 shrink-0 rounded-full ring-1 ring-line-strong"
                  style={{ background: color }}
                />
                <span className="font-semibold">{who(p)}</span>
                <span className="text-fg-3">
                  {t('teams.onScreen', { time: formatTimecode(p.seconds * 1000) })}
                </span>
              </>
            );
            const base =
              'inline-flex items-center gap-1.5 rounded-full border border-line bg-surface-2 px-3 py-1.5 text-control';
            return (
              <li key={p.track_id}>
                {readOnly ? (
                  <span className={base}>{chip}</span>
                ) : (
                  <DropdownMenu modal={false}>
                    <DropdownMenuTrigger asChild>
                      <button
                        type="button"
                        aria-label={t('teams.fixFor', { who: who(p) })}
                        className={`${base} focus-ring hover:bg-surface-3`}
                      >
                        {chip}
                        <ChevronDown size={14} aria-hidden="true" />
                      </button>
                    </DropdownMenuTrigger>
                    <DropdownMenuContent>
                      {teams.map((team) => (
                        <DropdownMenuItem
                          key={team.cluster}
                          onSelect={() =>
                            fix.mutate({
                              track_id: p.track_id,
                              role: 'goalkeeper',
                              team: team.cluster,
                            })
                          }
                        >
                          {t('teams.asGoalkeeper', { team: team.name })}
                        </DropdownMenuItem>
                      ))}
                      <DropdownMenuItem
                        onSelect={() => fix.mutate({ track_id: p.track_id, role: 'referee' })}
                      >
                        {t('teams.asReferee')}
                      </DropdownMenuItem>
                    </DropdownMenuContent>
                  </DropdownMenu>
                )}
              </li>
            );
          })}
        </ul>
      )}
    </section>
  );
}
