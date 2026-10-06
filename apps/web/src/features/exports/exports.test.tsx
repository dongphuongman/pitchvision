import { screen, waitFor, within } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import type { ExportOut } from '@/api/exports';
import { mockJob, mockVideo } from '@/mocks/videos';
import { setupIdentityMock } from '@/test/msw';
import { renderApp, testSession } from '@/test/renderApp';
import { daysLeft, parseTimecode } from './model';

const mock = setupIdentityMock();

function seed() {
  const v = mockVideo({ title: 'France vs Switzerland', status: 'ready', duration_ms: 120_000 });
  mock.videos.videos.push(v);
  mock.videos.jobs.push(
    mockJob(v, { status: 'done', progress: 100, pitch_calibration: 'unavailable' }),
  );
  return v;
}

function exportRow(partial: Partial<ExportOut>): ExportOut {
  return {
    id: 'exp-x',
    video_id: 'v-x',
    video_title: 'France vs Switzerland',
    kind: 'video',
    status: 'done',
    progress: 100,
    filename: 'fra_sui_full.mp4',
    size_bytes: 1_100_000_000,
    from_ms: 0,
    to_ms: 120_000,
    layout: 'video',
    quality: 720,
    watermark: true,
    layers: {},
    error: null,
    created_by: null,
    created_at: new Date().toISOString(),
    finished_at: new Date().toISOString(),
    expires_at: new Date(Date.now() + 6.5 * 86_400_000).toISOString(),
    ...partial,
  };
}

describe('export helpers', () => {
  it('parses timecodes and counts days left', () => {
    expect(parseTimecode('34:52')).toBe(2_092_000);
    expect(parseTimecode('1:02:07')).toBe(3_727_000);
    expect(parseTimecode('90')).toBe(90_000);
    expect(parseTimecode('ab')).toBeNull();
    expect(parseTimecode('1:2:3:4')).toBeNull();
    expect(daysLeft(null)).toBeNull();
    expect(daysLeft('2026-10-10T12:00:00Z', Date.parse('2026-10-06T00:00:00Z'))).toBe(4);
    expect(daysLeft('2026-10-01T00:00:00Z', Date.parse('2026-10-06T00:00:00Z'))).toBe(0);
  });
});

describe('SCR-25 Export dialog', () => {
  it('starts a custom-range export with the chosen layers (Free: watermark notice, no map)', async () => {
    const v = seed();
    const user = userEvent.setup();
    renderApp(`/videos/${v.id}/overview`, { auth: testSession({ role: 'viewer' }) });
    await user.click(await screen.findByRole('button', { name: 'Export' }));
    const dialog = await screen.findByRole('dialog', { name: 'Export' });
    expect(within(dialog).getByText('Free plan exports include a watermark.')).toBeInTheDocument();
    expect(within(dialog).getByRole('radio', { name: 'Video + map side by side' })).toBeDisabled();
    await user.click(within(dialog).getByRole('radio', { name: 'Custom' }));
    const from = within(dialog).getByRole('textbox', { name: 'From' });
    const to = within(dialog).getByRole('textbox', { name: 'To' });
    await user.clear(from);
    await user.type(from, '0:30');
    await user.clear(to);
    await user.type(to, '0:10');
    expect(within(dialog).getByRole('button', { name: 'Start export' })).toBeDisabled();
    await user.clear(to);
    await user.type(to, '1:15');
    await user.click(within(dialog).getByRole('checkbox', { name: 'Trails' }));
    await user.click(within(dialog).getByRole('button', { name: 'Start export' }));
    await waitFor(() => expect(screen.queryByRole('dialog')).not.toBeInTheDocument());
    expect(await screen.findByText('Export started')).toBeInTheDocument();
    const sent = mock.videos.log.find((l) => l.startsWith('POST /api/v1/exports'));
    const body = JSON.parse(sent?.slice('POST /api/v1/exports '.length) ?? '{}') as Record<
      string,
      unknown
    >;
    expect(body).toMatchObject({
      video_id: v.id,
      from_ms: 30_000,
      to_ms: 75_000,
      layout: 'video',
      quality: 720,
    });
    expect(body.layers).toMatchObject({ boxes: true, trails: true });
  });
});

describe('SCR-26 Exports', () => {
  it('shows the empty state', async () => {
    renderApp('/exports', { auth: testSession() });
    expect(
      await screen.findByText('No exports yet. Exports from any match appear here.'),
    ).toBeInTheDocument();
    expect(screen.getByRole('link', { name: 'Go to library' })).toHaveAttribute('href', '/library');
  });

  it('lists exports with status, expiry and actions; cancel, retry and delete', async () => {
    mock.account.exports.push(
      exportRow({ id: 'e-done' }),
      exportRow({ id: 'e-run', status: 'running', progress: 64, filename: 'tracks.mp4' }),
      exportRow({ id: 'e-fail', status: 'failed', filename: 'training.mp4', expires_at: null }),
    );
    const user = userEvent.setup();
    renderApp('/exports', { auth: testSession() });
    const done = (await screen.findByText('fra_sui_full.mp4')).closest('tr');
    expect(done).not.toBeNull();
    expect(within(done as HTMLElement).getByText('Ready')).toBeInTheDocument();
    expect(within(done as HTMLElement).getByText('6 days')).toBeInTheDocument();
    expect(
      within(done as HTMLElement).getByRole('button', { name: 'Download' }),
    ).toBeInTheDocument();
    const running = screen.getByText('tracks.mp4').closest('tr') as HTMLElement;
    expect(within(running).getByText('64%')).toBeInTheDocument();
    await user.click(within(running).getByRole('button', { name: 'Cancel' }));
    await waitFor(() => expect(mock.videos.log).toContain('POST /api/v1/exports/e-run/cancel'));
    const failed = screen.getByText('training.mp4').closest('tr') as HTMLElement;
    await user.click(within(failed).getByRole('button', { name: 'Retry' }));
    await waitFor(() => expect(mock.videos.log).toContain('POST /api/v1/exports/e-fail/retry'));

    await user.click(screen.getByRole('button', { name: 'More actions for fra_sui_full.mp4' }));
    await user.click(await screen.findByRole('menuitem', { name: 'Delete' }));
    const confirm = await screen.findByRole('dialog', { name: 'Delete' });
    await user.click(within(confirm).getByRole('button', { name: 'Delete' }));
    await waitFor(() => expect(mock.videos.log).toContain('DELETE /api/v1/exports/e-done'));
    await waitFor(() => expect(screen.queryByText('fra_sui_full.mp4')).not.toBeInTheDocument());
  });
});

describe('G.E06 usage meter + SCR-51 notifications', () => {
  it('shows minutes used and the unread notifications; opening one marks it read', async () => {
    const v = seed();
    mock.account.usage.minutes_used = 100; // ≥ 80 % → warning colour
    mock.account.notifications.push(
      {
        id: 'n-1',
        type: 'job_ready',
        video_id: v.id,
        video_title: 'France vs Switzerland',
        data: {},
        path: `/videos/${v.id}`,
        created_at: new Date(Date.now() - 120_000).toISOString(),
        read_at: null,
      },
      {
        id: 'n-2',
        type: 'export_ready',
        video_id: v.id,
        video_title: 'France vs Switzerland',
        data: { filename: 'fra_sui_full.mp4' },
        path: '/exports',
        created_at: new Date(Date.now() - 3_600_000).toISOString(),
        read_at: null,
      },
    );
    const user = userEvent.setup();
    const { router } = renderApp('/library', { auth: testSession() });
    const meter = await screen.findByRole('link', {
      name: 'Analysis minutes this month: 100 of 120',
    });
    expect(meter).toHaveTextContent('100/120 min');
    expect(meter).toHaveClass('text-warn');
    await user.click(screen.getByRole('button', { name: 'Notifications · 2 unread' }));
    expect(
      await screen.findByRole('menuitem', { name: /France vs Switzerland is ready/ }),
    ).toBeInTheDocument();
    await user.click(screen.getByRole('menuitem', { name: /Export fra_sui_full.mp4 is ready/ }));
    await waitFor(() => expect(router.state.location.pathname).toBe('/exports'));
    expect(mock.videos.log).toContain('POST /api/v1/notifications/read n-2');
  });
});
