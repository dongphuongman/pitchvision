import { screen, waitFor, within } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { http, HttpResponse } from 'msw';
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

  it('SCR-20a: match stats per team over the observed time, estimates labelled (M3)', async () => {
    const v = seed({ status: 'done', progress: 100, pitch_calibration: 'ok' });
    renderApp(`/videos/${v.id}/overview`, { auth: testSession() });
    const stats = await screen.findByRole('region', { name: 'Match stats' });
    expect(
      await within(stats).findByText(/Tactical data for \d+% of match time/),
    ).toBeInTheDocument();
    const distance = within(stats).getByRole('row', { name: /^Distance/ });
    expect(within(distance).getByText('2.7 km')).toBeInTheDocument(); // observed (mock: Σ team A tracks)
    expect(within(distance).getAllByText('Estimated')).toHaveLength(2);
    expect(within(stats).getByRole('row', { name: /^Top speed/ })).toHaveTextContent(
      /33\.5 km\/h.*Track 1/,
    );
    expect(within(stats).getByRole('row', { name: /^Speed zones/ })).toHaveTextContent(
      '8% sprinting',
    ); // M3-CV-02: possession + passes + possession over time (a minute per bar, click = watch it)
    expect(within(stats).getByRole('row', { name: /^Possession/ })).toHaveTextContent(/58%.*42%/);
    expect(within(stats).getByRole('row', { name: /^Passes/ })).toHaveTextContent(
      '8 · 75% completed · 2 lost',
    );
    expect(within(stats).getAllByRole('listitem')).toHaveLength(12);
    expect(
      within(stats).getByRole('listitem', { name: /^0:00: Riverside FC \d+%/ }),
    ).toBeInTheDocument();
  });

  it('SCR-20d Passing: pass map + list for the chosen team, lost balls can be hidden', async () => {
    const v = seed({ status: 'done', progress: 100, pitch_calibration: 'ok' });
    const { router } = renderApp(`/videos/${v.id}/passing`, { auth: testSession() });
    expect(await screen.findByTestId('pass-map')).toHaveAccessibleName(
      'Pass map: 8 passes on the pitch',
    );
    const list = screen.getByRole('list', { name: 'Passes' });
    expect(within(list).getAllByRole('listitem')).toHaveLength(8);
    expect(within(list).getAllByLabelText('Lost')).toHaveLength(2);
    const user = userEvent.setup();
    await user.click(screen.getByRole('checkbox', { name: 'Show lost balls' }));
    expect(within(list).getAllByRole('listitem')).toHaveLength(6);
    expect(within(list).getAllByRole('button', { name: /^Watch the pass at / })).toHaveLength(6);
    await user.click(screen.getByRole('combobox', { name: 'Team' }));
    await user.click(await screen.findByRole('option', { name: 'Harbor City' }));
    await waitFor(() => expect(router.state.location.search).toMatchObject({ team: 1 }));
    expect(await screen.findByText('3 passes · 3 completed')).toBeInTheDocument();
  });

  it('SCR-20c Heatmaps: team / track filters go to the URL and the request; average positions', async () => {
    const v = seed({ status: 'done', progress: 100, pitch_calibration: 'ok' });
    const seen: string[] = [];
    mock.server.events.on('request:start', ({ request }) => {
      if (request.url.includes('/heatmap')) seen.push(new URL(request.url).search);
    });
    const { router } = renderApp(`/videos/${v.id}/heatmaps`, { auth: testSession() });
    expect(await screen.findByTestId('heatmap')).toHaveAccessibleName(
      'Heatmap of Both teams on the pitch',
    );
    const user = userEvent.setup();
    await user.click(screen.getByRole('combobox', { name: 'Team' }));
    await user.click(await screen.findByRole('option', { name: 'Harbor City' }));
    await waitFor(() => expect(router.state.location.search).toMatchObject({ team: 1 }));
    await waitFor(() => expect(seen.at(-1)).toBe('?cluster=1'));
    await user.click(screen.getByRole('combobox', { name: 'Track' }));
    await user.click(await screen.findByRole('option', { name: /^Track 102 · seen/ }));
    await waitFor(() => expect(seen.at(-1)).toBe('?cluster=1&track_id=102'));
    expect(screen.getByTestId('heatmap')).toHaveAccessibleName('Heatmap of track 102 on the pitch');
    await user.click(screen.getByRole('checkbox', { name: 'Average positions' }));
    await waitFor(() =>
      expect(router.state.location.search).toMatchObject({ team: 1, track: 102, avg: 1 }),
    );
    expect(screen.getByRole('button', { name: 'Download PNG' })).toBeEnabled();
  });

  it('< 1024 px: Video / Map / Both chooses which pane shows (UIUX §09)', async () => {
    const v = seed({ status: 'done', progress: 100, pitch_calibration: 'ok' });
    const user = userEvent.setup();
    renderApp(`/videos/${v.id}/overview`, { auth: testSession() });
    const map = await screen.findByTestId('tactical-map');
    const video = await screen.findByTestId('workspace-video');
    const panes = screen.getByRole('radiogroup', { name: 'Show' });
    const hidden = (el: HTMLElement) => !!el.closest('.max-lg\\:hidden');
    expect([hidden(video), hidden(map)]).toEqual([false, false]);
    await user.click(within(panes).getByRole('radio', { name: 'Map' }));
    expect([hidden(video), hidden(map)]).toEqual([true, false]);
    await user.click(within(panes).getByRole('radio', { name: 'Video' }));
    expect([hidden(video), hidden(map)]).toEqual([false, true]);
  });

  it('"Map the pitch automatically" starts a repitch job when the workers have a pitch model', async () => {
    const v = seed({ status: 'done', progress: 100, pitch_calibration: 'unavailable' });
    let posted = '';
    mock.server.use(
      http.get('*/api/v1/meta', () =>
        HttpResponse.json({ version: 't', schema_version: 1, pitch_model: true }),
      ),
      http.post('*/api/v1/results/:id/repitch', ({ params, request }) => {
        posted = `${String(params.id)} ${request.headers.get('Idempotency-Key') ? 'idem' : 'none'}`;
        return HttpResponse.json(mockJob(v, { status: 'queued', progress: 0 }), { status: 202 });
      }),
    );
    const user = userEvent.setup();
    const { router } = renderApp(`/videos/${v.id}/overview`, { auth: testSession() });
    await user.click(await screen.findByRole('button', { name: 'Map the pitch automatically' }));
    await waitFor(() => expect(router.state.location.pathname).toBe(`/videos/${v.id}/processing`));
    expect(posted).toBe(`${v.id} idem`);
  });

  it('hides "Map the pitch" when no pitch model is available', async () => {
    const v = seed({ status: 'done', progress: 100, pitch_calibration: 'unavailable' });
    mock.server.use(
      http.get('*/api/v1/meta', () =>
        HttpResponse.json({ version: 't', schema_version: 1, pitch_model: false }),
      ),
    );
    renderApp(`/videos/${v.id}/overview`, { auth: testSession() });
    await screen.findByRole('link', { name: 'Calibrate' });
    expect(
      screen.queryByRole('button', { name: 'Map the pitch automatically' }),
    ).not.toBeInTheDocument();
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
