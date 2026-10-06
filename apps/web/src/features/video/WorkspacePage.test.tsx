import { screen, waitFor, within } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { mockJob, mockVideo, type MockJob } from '@/mocks/videos';
import { setupIdentityMock } from '@/test/msw';
import { renderApp, testSession } from '@/test/renderApp';

const mock = setupIdentityMock();

function seed(job: Partial<MockJob> | null) {
  const v = mockVideo({ title: 'France vs Switzerland', status: 'ready', duration_ms: 120_000 });
  mock.videos.videos.push(v);
  if (job) mock.videos.jobs.push(mockJob(v, job));
  return v;
}

describe('SCR-20 Analysis workspace', () => {
  it('loads manifest + media and shows the player, timeline, layers and overview', async () => {
    const v = seed({ status: 'done', progress: 100, pitch_calibration: 'ok' });
    renderApp(`/videos/${v.id}/overview`, { auth: testSession() });
    expect(await screen.findByText('France vs Switzerland')).toBeInTheDocument();
    expect(await screen.findByTestId('workspace-video')).toHaveAttribute(
      'src',
      `https://storage.mock/${v.id}/normalized.mp4`,
    );
    expect(screen.getByTestId('tactical-map')).toBeInTheDocument();
    expect(screen.getByRole('checkbox', { name: 'Boxes' })).toBeChecked();
    expect(screen.getByRole('button', { name: 'Play' })).toBeInTheDocument();
    expect(
      await screen.findByText(/Wide-angle coverage \d+% · Pitch mapping: good/),
    ).toBeInTheDocument();
    expect(screen.queryByText(/tactical map is empty/)).not.toBeInTheDocument();
  });

  it('explains a missing pitch mapping', async () => {
    const v = seed({ status: 'done', progress: 100, pitch_calibration: 'unavailable' });
    renderApp(`/videos/${v.id}/overview`, { auth: testSession() });
    expect(await screen.findByText(/the tactical map is empty/)).toBeInTheDocument();
  });

  it('points to processing while there are no results', async () => {
    const v = seed({ status: 'inferring', stage: 'infer', progress: 40 });
    renderApp(`/videos/${v.id}/overview`, { auth: testSession() });
    expect(await screen.findByText(/Results aren't ready yet/)).toBeInTheDocument();
    expect(screen.getByRole('link', { name: 'Open processing' })).toHaveAttribute(
      'href',
      `/videos/${v.id}/processing`,
    );
  });

  it('offers Teams & colors and Calibrate pitch to editors (E05b/E05c) and a Calibrate banner (E06)', async () => {
    const v = seed({ status: 'done', progress: 100, pitch_calibration: 'degraded' });
    mock.videos.teams.set(v.id, [
      { id: 't-0', cluster: 0, name: 'Team A', color: '#ffffff', confirmed: false },
      { id: 't-1', cluster: 1, name: 'Team B', color: '#3e8bff', confirmed: false },
    ]);
    const user = userEvent.setup();
    const { router } = renderApp(`/videos/${v.id}/overview?t=12`, {
      auth: testSession({ role: 'editor' }),
    });
    const banner = (await screen.findByText(/Pitch quality is low/)).closest('[role="status"]');
    expect(banner).not.toBeNull();
    const calibrate = within(banner as HTMLElement).getByRole('link', { name: 'Calibrate' });
    expect(calibrate.getAttribute('href')).toContain(`/videos/${v.id}/calibrate?`);
    // return_to = this tab (+ ?t=, which the player rewrites once paused).
    expect(screen.getByRole('link', { name: 'Confirm teams' }).getAttribute('href')).toMatch(
      new RegExp(
        `^/videos/${v.id}/teams\\?return_to=${encodeURIComponent(`/videos/${v.id}/overview`)}`,
      ),
    );

    await user.click(screen.getByRole('button', { name: 'More actions' }));
    const menu = await screen.findByRole('menu');
    expect(within(menu).getByRole('menuitem', { name: 'Calibrate pitch' })).toBeInTheDocument();
    await user.click(within(menu).getByRole('menuitem', { name: 'Teams & colors' }));
    await waitFor(() => expect(router.state.location.pathname).toBe(`/videos/${v.id}/teams`));
    expect(await screen.findByRole('heading', { name: 'Confirm teams' })).toBeInTheDocument();
  });

  it('hides the correction entry points from viewers', async () => {
    const v = seed({ status: 'done', progress: 100, pitch_calibration: 'degraded' });
    renderApp(`/videos/${v.id}/overview`, { auth: testSession({ role: 'viewer' }) });
    expect(await screen.findByText(/Pitch quality is low/)).toBeInTheDocument();
    expect(screen.queryByRole('link', { name: 'Calibrate' })).not.toBeInTheDocument();
    expect(screen.queryByRole('button', { name: 'More actions' })).not.toBeInTheDocument();
  });
});
