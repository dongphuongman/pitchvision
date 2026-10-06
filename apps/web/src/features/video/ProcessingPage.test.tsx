import { act, screen, waitFor } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { emitWs, mockJob, mockVideo, wsTopics, type MockJob } from '@/mocks/videos';
import { setupIdentityMock } from '@/test/msw';
import { renderApp, testSession } from '@/test/renderApp';

const mock = setupIdentityMock();

function seedVideo(video: Parameters<typeof mockVideo>[0] = {}) {
  const v = mockVideo({ title: 'France vs Switzerland', status: 'ready', ...video });
  mock.videos.videos.push(v);
  return v;
}

function seedJob(job: Partial<MockJob>) {
  const v = seedVideo();
  const j = mockJob(v, job);
  mock.videos.jobs.push(j);
  return { v, j };
}

describe('SCR-13 Processing', () => {
  it('shows the current step, live progress and segments from the WebSocket', async () => {
    const { v, j } = seedJob({ status: 'inferring', stage: 'infer', progress: 40 });
    renderApp(`/videos/${v.id}/processing`, { auth: testSession() });
    expect(
      await screen.findByRole('heading', { name: 'France vs Switzerland' }),
    ).toBeInTheDocument();
    await waitFor(() => expect(wsTopics(mock.videos)).toContain(`job:${j.id}`));
    expect(screen.getByText('Analyze').closest('li')).toHaveAttribute('aria-current', 'step');

    act(() => {
      emitWs(mock.videos, {
        topic: `job:${j.id}`,
        type: 'progress',
        data: {
          stage: 'infer',
          pct: 62.4,
          fps: 96.1,
          eta_s: 540,
          segments: { done: 19, running: 4, total: 45 },
        },
      });
    });
    expect(await screen.findByText(/62% · 96 fps · about/)).toBeInTheDocument();
    expect(screen.getByRole('img', { name: '19 of 45 segments analyzed' })).toBeInTheDocument();
  });

  it('cancels after a second click', async () => {
    const { v, j } = seedJob({ status: 'calibrating', stage: 'calibrate', progress: 10 });
    const user = userEvent.setup();
    renderApp(`/videos/${v.id}/processing`, { auth: testSession() });
    await user.click(await screen.findByRole('button', { name: 'Cancel analysis' }));
    expect(mock.videos.log.some((l) => l.includes('/cancel'))).toBe(false);
    await user.click(screen.getByRole('button', { name: 'Click again to cancel' }));
    await waitFor(() => expect(mock.videos.log).toContain(`POST /api/v1/jobs/${j.id}/cancel`));
    expect(await screen.findByText('This analysis was cancelled.')).toBeInTheDocument();
  });

  it('explains a failure and retries a retryable one', async () => {
    const { v, j } = seedJob({
      status: 'failed',
      stage: 'infer',
      progress: 55,
      error: { code: 'INTERNAL', retryable: true, detail: 'infer:023 failed 3 times' },
    });
    const user = userEvent.setup();
    renderApp(`/videos/${v.id}/processing`, { auth: testSession() });
    expect(await screen.findByText('Analysis failed at “Analyze”.')).toBeInTheDocument();
    expect(screen.getByText(/infer:023 failed 3 times/)).toBeInTheDocument();
    await user.click(screen.getByRole('button', { name: 'Retry' }));
    await waitFor(() => expect(mock.videos.log).toContain(`POST /api/v1/jobs/${j.id}/retry`));
  });

  it('does not offer retry for a non-retryable error', async () => {
    const { v } = seedJob({
      status: 'failed',
      stage: 'calibrate',
      error: { code: 'TEAM_SEPARATION_FAILED', retryable: false, detail: null },
    });
    renderApp(`/videos/${v.id}/processing`, { auth: testSession() });
    expect(await screen.findByText('Analysis failed at “Detect teams”.')).toBeInTheDocument();
    expect(screen.queryByRole('button', { name: 'Retry' })).not.toBeInTheDocument();
  });

  it('opens the workspace when the job completes (A01)', async () => {
    const { v, j } = seedJob({ status: 'postprocessing', stage: 'tiles', progress: 96 });
    const { router } = renderApp(`/videos/${v.id}/processing`, { auth: testSession() });
    await waitFor(() => expect(wsTopics(mock.videos)).toContain(`job:${j.id}`));
    j.status = 'done';
    j.progress = 100;
    act(() => {
      emitWs(mock.videos, { topic: `job:${j.id}`, type: 'completed', data: { video_id: v.id } });
    });
    await waitFor(() => expect(router.state.location.pathname).toBe(`/videos/${v.id}/overview`));
  });

  it('offers to start an analysis for a stored video without a job', async () => {
    const v = seedVideo();
    const user = userEvent.setup();
    renderApp(`/videos/${v.id}/processing`, { auth: testSession() });
    await user.click(await screen.findByRole('button', { name: 'Start analysis' }));
    await waitFor(() =>
      expect(mock.videos.log.some((l) => l.startsWith(`POST jobs ${v.id}`))).toBe(true),
    );
  });

  it('offers manual fallbacks for team / pitch failures to editors (E10, E11)', async () => {
    const { v } = seedJob({
      status: 'failed',
      stage: 'calibrate',
      error: { code: 'TEAM_SEPARATION_FAILED', retryable: false, detail: null },
    });
    renderApp(`/videos/${v.id}/processing`, { auth: testSession({ role: 'editor' }) });
    const link = await screen.findByRole('link', { name: 'Pick players manually' });
    expect(link.getAttribute('href')).toContain(`/videos/${v.id}/teams?mode=manual`);
  });

  it('links HOMOGRAPHY_UNAVAILABLE to manual calibration, not for viewers', async () => {
    const { v } = seedJob({
      status: 'failed',
      stage: 'calibrate',
      error: { code: 'HOMOGRAPHY_UNAVAILABLE', retryable: false, detail: null },
    });
    const first = renderApp(`/videos/${v.id}/processing`, { auth: testSession() });
    const link = await screen.findByRole('link', { name: 'Calibrate pitch manually' });
    expect(link.getAttribute('href')).toContain(`/videos/${v.id}/calibrate`);
    first.unmount();

    renderApp(`/videos/${v.id}/processing`, { auth: testSession({ role: 'viewer' }) });
    expect(await screen.findByText(/Analysis failed at/)).toBeInTheDocument();
    expect(
      screen.queryByRole('link', { name: 'Calibrate pitch manually' }),
    ).not.toBeInTheDocument();
  });
});
