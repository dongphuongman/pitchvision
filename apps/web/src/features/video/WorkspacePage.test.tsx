import { screen } from '@testing-library/react';
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
});
