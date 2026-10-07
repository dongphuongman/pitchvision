import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { Trash2 } from 'lucide-react';
import { useState } from 'react';
import { useTranslation } from 'react-i18next';
import {
  deleteSavedTeam,
  savedTeamsKey,
  savedTeamsQuery,
  updateSavedTeam,
  type SavedTeam,
} from '@/api/savedTeams';
import { isApiError } from '@/api/errors';
import { useApiErrorMessage } from '@/api/useApiErrorMessage';
import { Button, Card, IconButton, Input, Skeleton, toast } from '@/ui';

const NAME_MAX = 40;

/** SCR-40 O03 Saved teams (admin+): names + colours remembered from SCR-14, edited / deleted inline. */
export function SavedTeamsCard() {
  const { t } = useTranslation('settings');
  const q = useQuery(savedTeamsQuery);
  const items = q.data ?? [];
  return (
    <Card className="flex flex-col gap-3">
      <b>{t('organization.savedTeams')}</b>
      <p className="m-0 text-fg-2">{t('organization.savedTeamsHelp')}</p>
      {q.isPending ? (
        <Skeleton className="h-10" />
      ) : items.length === 0 ? (
        <p className="m-0 text-fg-3">{t('organization.noSavedTeams')}</p>
      ) : (
        <ul className="m-0 flex list-none flex-col gap-2 p-0">
          {items.map((team) => (
            <SavedTeamRow key={team.id} team={team} />
          ))}
        </ul>
      )}
    </Card>
  );
}

function SavedTeamRow({ team }: { team: SavedTeam }) {
  const { t } = useTranslation('settings');
  const qc = useQueryClient();
  const [name, setName] = useState(team.name);
  const [color, setColor] = useState(team.color);
  const dirty = name.trim() !== team.name || color !== team.color;
  const refresh = () => void qc.invalidateQueries({ queryKey: savedTeamsKey });
  const save = useMutation({
    mutationFn: () =>
      updateSavedTeam(team.id, {
        ...(name.trim() !== team.name ? { name: name.trim() } : {}),
        ...(color !== team.color ? { color } : {}),
      }),
    onSuccess: () => toast({ title: t('organization.savedTeamSaved'), tone: 'success' }),
    onSettled: refresh,
  });
  const remove = useMutation({ mutationFn: () => deleteSavedTeam(team.id), onSettled: refresh });
  const generic = useApiErrorMessage(save.error);
  const error =
    isApiError(save.error) && save.error.code === 'CONFLICT'
      ? t('organization.nameTaken')
      : generic;

  return (
    <li className="flex flex-col gap-1">
      <div className="flex items-center gap-2">
        <input
          type="color"
          value={color}
          aria-label={t('organization.teamColor', { name: team.name })}
          className="size-9 shrink-0 cursor-pointer rounded-md border border-line bg-transparent p-0.5"
          onChange={(e) => setColor(e.target.value.toLowerCase())}
        />
        <Input
          value={name}
          maxLength={NAME_MAX}
          aria-label={t('organization.teamName', { name: team.name })}
          onChange={(e) => setName(e.target.value)}
        />
        {dirty && (
          <Button
            size="sm"
            disabled={!name.trim()}
            loading={save.isPending}
            onClick={() => save.mutate()}
          >
            {t('profile.save')}
          </Button>
        )}
        <IconButton
          label={t('organization.deleteTeam', { name: team.name })}
          icon={<Trash2 size={16} />}
          onClick={() => remove.mutate()}
        />
      </div>
      {save.isError && <span className="text-caption text-danger">{error}</span>}
    </li>
  );
}
