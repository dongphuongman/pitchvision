import { fireEvent, screen, waitFor, within } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { http, HttpResponse } from 'msw';
import { mockJob, mockVideo, type MockJob } from '@/mocks/videos';
import { setupIdentityMock } from '@/test/msw';
import { renderApp, testSession } from '@/test/renderApp';

const mock = setupIdentityMock();

function seed(job: Partial<MockJob> = { status: 'done', progress: 100 }) {
  const v = mockVideo({ title: 'France vs Switzerland', status: 'ready', duration_ms: 120_000 });
  mock.videos.videos.push(v);
  mock.videos.jobs.push(mockJob(v, job));
  return v;
}

/** Request bodies logged by the mock as `<METHOD> <what> <videoId> <json>`. */
function logged(prefix: string): unknown[] {
  return mock.videos.log
    .filter((l) => l.startsWith(prefix))
    .map((l) => JSON.parse(l.slice(l.indexOf('{'))) as unknown);
}

describe('SCR-14 Team confirmation', () => {
  it('renames, recolours and swaps the teams, then confirms (PATCH) and goes back', async () => {
    const v = seed();
    const user = userEvent.setup();
    const { router } = renderApp(`/videos/${v.id}/teams`, { auth: testSession() });

    const team1 = await screen.findByRole('region', { name: 'Team 1' });
    const team2 = screen.getByRole('region', { name: 'Team 2' });
    expect(within(team1).getAllByRole('img', { name: /Player sample/ })).toHaveLength(8);
    expect(within(team1).getByRole('list', { name: 'Players grouped into team 1' })).toBeVisible();

    const name1 = within(team1).getByLabelText('Team name');
    expect(name1).toHaveValue('Team A');
    await user.clear(name1);
    await user.type(name1, 'Riverside FC');
    await user.click(within(team2).getByRole('button', { name: 'Color #f2c94c' }));
    expect(within(team2).getByRole('button', { name: 'Color #f2c94c' })).toHaveAttribute(
      'aria-pressed',
      'true',
    );

    await user.click(screen.getByRole('button', { name: 'Swap teams' }));
    expect(within(team1).getByLabelText('Team name')).toHaveValue('Team B');
    expect(within(team2).getByLabelText('Team name')).toHaveValue('Riverside FC');
    expect(screen.getByText('Team names and colors swapped.')).toBeInTheDocument();

    await user.click(screen.getByRole('button', { name: 'Confirm teams' }));
    await waitFor(() => expect(logged('PATCH teams')).toHaveLength(1));
    expect(logged('PATCH teams')[0]).toEqual({
      teams: [
        { cluster: 0, name: 'Team B', color: '#f2c94c' },
        { cluster: 1, name: 'Riverside FC', color: '#e5484d' },
      ],
      swap: false,
      confirmed: true,
    });
    await waitFor(() => expect(router.state.location.pathname).toBe(`/videos/${v.id}/overview`));
  });

  it('picks a custom colour, validates names and returns to `return_to`', async () => {
    const v = seed();
    const user = userEvent.setup();
    const { router } = renderApp(
      `/videos/${v.id}/teams?return_to=${encodeURIComponent(`/videos/${v.id}/processing`)}`,
      { auth: testSession({ role: 'editor' }) },
    );
    const team2 = await screen.findByRole('region', { name: 'Team 2' });
    fireEvent.change(within(team2).getByLabelText('Custom color'), {
      target: { value: '#123456' },
    });
    await user.clear(within(team2).getByLabelText('Team name'));
    await user.click(screen.getByRole('button', { name: 'Confirm teams' }));
    expect(await within(team2).findByText('Enter 1–40 characters.')).toBeInTheDocument();
    expect(logged('PATCH teams')).toHaveLength(0);

    await user.type(within(team2).getByLabelText('Team name'), 'Harbor City');
    await user.click(screen.getByRole('button', { name: 'Confirm teams' }));
    await waitFor(() => expect(router.state.location.pathname).toBe(`/videos/${v.id}/processing`));
    expect(logged('PATCH teams')[0]).toMatchObject({
      teams: [{ cluster: 0 }, { cluster: 1, name: 'Harbor City', color: '#123456' }],
    });
  });

  it('warns when both colours are nearly the same', async () => {
    const v = seed();
    const user = userEvent.setup();
    renderApp(`/videos/${v.id}/teams`, { auth: testSession() });
    const team1 = await screen.findByRole('region', { name: 'Team 1' });
    const team2 = screen.getByRole('region', { name: 'Team 2' });
    await user.click(within(team1).getByRole('button', { name: 'Color #ffffff' }));
    expect(screen.queryByText(/colors are very similar/)).not.toBeInTheDocument();
    await user.click(within(team2).getByRole('button', { name: 'Color #ffffff' }));
    expect(screen.getByText(/colors are very similar/)).toBeInTheDocument();
  });

  it('shows the server error when the analysis has no teams yet (409 no_teams)', async () => {
    const v = seed();
    mock.server.use(
      http.patch('*/api/v1/results/:id/teams', () =>
        HttpResponse.json(
          {
            type: 'about:blank',
            title: 'No teams',
            status: 409,
            code: 'CONFLICT',
            reason: 'no_teams',
          },
          { status: 409, headers: { 'Content-Type': 'application/problem+json' } },
        ),
      ),
    );
    const user = userEvent.setup();
    renderApp(`/videos/${v.id}/teams`, { auth: testSession() });
    await user.click(await screen.findByRole('button', { name: 'Confirm teams' }));
    expect(await screen.findByRole('alert')).toHaveTextContent(
      'Teams are available once the analysis has finished.',
    );
  });

  it('is read-only for viewers', async () => {
    const v = seed();
    renderApp(`/videos/${v.id}/teams`, { auth: testSession({ role: 'viewer' }) });
    const team1 = await screen.findByRole('region', { name: 'Team 1' });
    expect(screen.getByText('Only editors can change teams.')).toBeInTheDocument();
    expect(within(team1).getByLabelText('Team name')).toHaveAttribute('readonly');
    expect(within(team1).getByRole('button', { name: 'Color #ffffff' })).toBeDisabled();
    expect(screen.queryByRole('button', { name: 'Confirm teams' })).not.toBeInTheDocument();
    expect(screen.queryByRole('button', { name: 'Swap teams' })).not.toBeInTheDocument();
    expect(
      screen.queryByRole('link', { name: 'These groups mix both teams' }),
    ).not.toBeInTheDocument();
  });
});

describe('SCR-14 manual mode', () => {
  it('assigns boxes to both teams (≥ 2 each), re-runs detection and opens processing', async () => {
    const v = seed();
    const user = userEvent.setup();
    const { router } = renderApp(`/videos/${v.id}/teams`, { auth: testSession() });
    await user.click(await screen.findByRole('link', { name: 'These groups mix both teams' }));
    expect(
      await screen.findByRole('heading', { name: 'Pick players for each team' }),
    ).toBeInTheDocument();
    expect(router.state.location.search).toMatchObject({ mode: 'manual' });

    const stage = await screen.findByRole('group', { name: /Sample frame 1 with 10 players/ });
    const submit = screen.getByRole('button', { name: 'Re-run team detection' });
    // Cycle: none → Team 1 → Team 2 → none.
    await user.click(within(stage).getByRole('button', { name: 'Player 1, not assigned' }));
    await user.click(within(stage).getByRole('button', { name: 'Player 2, not assigned' }));
    await user.click(within(stage).getByRole('button', { name: 'Player 3, not assigned' }));
    await user.click(within(stage).getByRole('button', { name: 'Player 3, Team 1' }));
    expect(within(stage).getByRole('button', { name: 'Player 3, Team 2' })).toBeInTheDocument();
    expect(screen.getByText('Team 1: 2 picked')).toBeInTheDocument();
    expect(screen.getByText('Team 2: 1 picked')).toBeInTheDocument();
    expect(submit).toBeDisabled();

    // Next sample frame; keyboard: 2 = Team 2.
    await user.click(screen.getByRole('button', { name: 'Next sample frame' }));
    expect(screen.getByText('Frame 2 of 20')).toBeInTheDocument();
    const stage2 = screen.getByRole('group', { name: /Sample frame 2/ });
    within(stage2).getByRole('button', { name: 'Player 4, not assigned' }).focus();
    await user.keyboard('2');
    expect(within(stage2).getByRole('button', { name: 'Player 4, Team 2' })).toBeInTheDocument();
    expect(screen.getByText('Team 2: 2 picked')).toBeInTheDocument();
    expect(submit).toBeEnabled();

    await user.click(submit);
    await waitFor(() => expect(logged('POST teams/manual')).toHaveLength(1));
    const body = logged('POST teams/manual')[0] as {
      samples: Record<'0' | '1', { t_ms: number; box: number[] }[]>;
    };
    expect(body.samples['0'].map((s) => s.t_ms)).toEqual([0, 0]);
    expect(body.samples['1'].map((s) => s.t_ms)).toEqual([0, 3000]);
    expect(body.samples['1'][1]?.box).toHaveLength(4);
    await waitFor(() => expect(router.state.location.pathname).toBe(`/videos/${v.id}/processing`));
    expect(mock.videos.jobs.some((j) => j.kind === 'team_rerun' && j.status === 'queued')).toBe(
      true,
    );
  });

  it('explains when an older analysis has no candidate frames', async () => {
    const v = seed({
      status: 'failed',
      error: { code: 'INTERNAL', retryable: true, detail: null },
    });
    renderApp(`/videos/${v.id}/teams?mode=manual`, { auth: testSession() });
    expect(await screen.findByText(/Manual picking isn't available/)).toBeInTheDocument();
  });

  it('cancel returns to the confirm mode', async () => {
    const v = seed();
    const user = userEvent.setup();
    const { router } = renderApp(`/videos/${v.id}/teams?mode=manual`, { auth: testSession() });
    await user.click(await screen.findByRole('button', { name: 'Cancel' }));
    await waitFor(() => expect(router.state.location.search).not.toHaveProperty('mode', 'manual'));
    expect(await screen.findByRole('heading', { name: 'Confirm teams' })).toBeInTheDocument();
  });
});
