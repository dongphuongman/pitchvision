import { queryOptions } from '@tanstack/react-query';
import { api } from './client';
import type { components } from './generated/openapi';

/**
 * Saved teams (SCR-40 O03, SCR-14 E02 suggestions): the org's team names + colours, remembered when
 * teams are confirmed. GET any member; PATCH / DELETE admin+ (409 `name_taken` on a rename clash).
 */
export type SavedTeam = components['schemas']['SavedTeamOut'];

export const savedTeamsKey = ['saved-teams'] as const;

export const savedTeamsQuery = queryOptions({
  queryKey: savedTeamsKey,
  queryFn: async ({ signal }) => {
    const { data } = await api.GET('/api/v1/saved-teams', { signal });
    return data ?? [];
  },
  staleTime: 60_000,
});

export async function updateSavedTeam(
  id: string,
  body: { name?: string; color?: string },
): Promise<SavedTeam> {
  const { data } = await api.PATCH('/api/v1/saved-teams/{team_id}', {
    params: { path: { team_id: id } },
    body,
  });
  if (!data) throw new Error('Empty saved team response');
  return data;
}

export async function deleteSavedTeam(id: string): Promise<void> {
  await api.DELETE('/api/v1/saved-teams/{team_id}', { params: { path: { team_id: id } } });
}
