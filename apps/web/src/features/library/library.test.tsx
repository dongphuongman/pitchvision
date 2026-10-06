import { QueryClient } from '@tanstack/react-query';
import { act, screen, waitFor, within } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { applySession } from '@/auth/session';
import type { Video } from '@/api/videos';
import { emitWs, mockJob, mockVideo, wsTopics, type MockJob } from '@/mocks/videos';
import { setupIdentityMock } from '@/test/msw';
import { renderApp, testSession } from '@/test/renderApp';
import { scheduleDelete, usePendingDeletes } from './pendingDeletes';

const mock = setupIdentityMock();

/** A source-ready video whose latest job is `job` (jobs don't change the video status). */
function seed(title: string, job?: Partial<MockJob>, video: Partial<Video> = {}) {
  const v = mockVideo({ title, status: 'ready', ...video });
  mock.videos.videos.push(v);
  const j = job ? mockJob(v, job) : null;
  if (j) mock.videos.jobs.push(j);
  return { v, j };
}

const DONE: Partial<MockJob> = { status: 'done', progress: 100, coverage: { main_wide_pct: 71 } };

function seedDone(n: number) {
  for (let i = 0; i < n; i += 1) {
    seed(`Match ${String(i).padStart(2, '0')}`, DONE, {
      created_at: new Date(Date.UTC(2026, 8, 1 + i)).toISOString(),
    });
  }
}

async function openMenu(title: string) {
  const user = userEvent.setup();
  const card = await screen.findByRole('article', { name: title });
  await user.click(within(card).getByRole('button', { name: `Actions for ${title}` }));
  return { user, card, menu: await screen.findByRole('menu') };
}

describe('SCR-10 Library', () => {
  it('shows the empty state with the New analysis CTAs', async () => {
    renderApp('/library');
    expect(await screen.findByText('Analyze your first match')).toBeInTheDocument();
    expect(screen.getByRole('link', { name: 'Paste a YouTube link' })).toHaveAttribute(
      'href',
      '/library?new=youtube',
    );
    expect(screen.getByRole('link', { name: 'Upload a file' })).toHaveAttribute(
      'href',
      '/library?new=upload',
    );
  });

  it('pages by cursor (20 per page) with Load more', async () => {
    seedDone(26);
    const user = userEvent.setup();
    renderApp('/library');
    expect(await screen.findByText('20+ matches')).toBeInTheDocument();
    expect(screen.getAllByRole('article')).toHaveLength(20);
    // Newest first; placeholders while there is no thumbnail.
    expect(screen.getAllByRole('article')[0]).toHaveAccessibleName('Match 25');
    expect(screen.getAllByTestId('thumb-placeholder')).toHaveLength(20);

    await user.click(screen.getByRole('button', { name: 'Load more' }));
    await waitFor(() => expect(screen.getAllByRole('article')).toHaveLength(26));
    expect(screen.getByText('26 matches')).toBeInTheDocument();
    expect(screen.queryByRole('button', { name: 'Load more' })).not.toBeInTheDocument();
    expect(mock.videos.log).toContain('GET /api/v1/videos cursor=20');
    // Cards read `latest_job` from the list: no per-video job / video requests.
    expect(mock.videos.log.filter((l) => !l.startsWith('GET /api/v1/videos cursor='))).toEqual([]);
  });

  it('shows "Low pitch quality" when the finished job has a degraded pitch calibration', async () => {
    const { v } = seed('Night game', { ...DONE, pitch_calibration: 'degraded' });
    seed('Day game', { ...DONE, pitch_calibration: 'ok' });
    renderApp('/library');
    const card = await screen.findByRole('article', { name: 'Night game' });
    expect(within(card).getByText('Ready · Low pitch quality')).toBeInTheDocument();
    expect(within(card).getByText('71% wide')).toBeInTheDocument();
    expect(within(card).getByRole('link', { name: 'Night game' })).toHaveAttribute(
      'href',
      `/videos/${v.id}/overview`,
    );
    const day = screen.getByRole('article', { name: 'Day game' });
    expect(within(day).getByText('Ready')).toBeInTheDocument();
    expect(within(day).queryByText(/Low pitch quality/)).not.toBeInTheDocument();
  });

  it('marks the card cancelled on the `cancelled` WS event and unsubscribes', async () => {
    const { v, j } = seed('Abandoned', { status: 'inferring', progress: 30 });
    const job = j ?? mockJob(v);
    renderApp('/library');
    const card = await screen.findByRole('article', { name: 'Abandoned' });
    await waitFor(() => expect(wsTopics(mock.videos)).toEqual([`job:${job.id}`]));
    act(() => {
      emitWs(mock.videos, {
        topic: `job:${job.id}`,
        type: 'cancelled',
        data: { video_id: v.id },
      });
    });
    expect(await within(card).findByText('Cancelled')).toBeInTheDocument();
    expect(within(card).queryByRole('progressbar')).not.toBeInTheDocument();
    await waitFor(() => expect(wsTopics(mock.videos)).toEqual([]));
  });

  it('shows the coverage chip and opens the workspace for done videos', async () => {
    const { v } = seed('France vs Switzerland', DONE, { duration_ms: 5_652_000 });
    renderApp('/library');
    const card = await screen.findByRole('article', { name: 'France vs Switzerland' });
    expect(await within(card).findByText('Ready')).toBeInTheDocument();
    expect(within(card).getByText('71% wide')).toBeInTheDocument();
    expect(within(card).getByText('1:34:12')).toBeInTheDocument();
    expect(within(card).getByRole('link', { name: 'France vs Switzerland' })).toHaveAttribute(
      'href',
      `/videos/${v.id}/overview`,
    );
  });

  it('streams job progress over the WebSocket into the card', async () => {
    const { v, j } = seed('U17 vs Ajax', { status: 'inferring', progress: 42, eta_s: 540 });
    const job = j ?? mockJob(v);
    renderApp('/library');
    const card = await screen.findByRole('article', { name: 'U17 vs Ajax' });
    expect(await within(card).findByText('Analyzing 42%')).toBeInTheDocument();
    // ETA isn't in `latest_job`: it arrives with the server's progress snapshot after `subscribed`.
    expect(await within(card).findByText('42% · ETA 9m')).toBeInTheDocument();
    expect(within(card).getByRole('link', { name: 'U17 vs Ajax' })).toHaveAttribute(
      'href',
      `/videos/${v.id}/processing`,
    );

    await waitFor(() => expect(wsTopics(mock.videos)).toEqual([`job:${job.id}`]));
    act(() => {
      emitWs(mock.videos, {
        topic: `job:${job.id}`,
        type: 'progress',
        data: { stage: 'infer', pct: 64.4, eta_s: 120 },
      });
    });
    expect(await within(card).findByText('Analyzing 64%')).toBeInTheDocument();
    expect(within(card).getByRole('progressbar', { name: 'Analysis progress' })).toHaveAttribute(
      'aria-valuenow',
      '64',
    );

    Object.assign(job, { status: 'done', progress: 100, coverage: { main_wide_pct: 64 } });
    act(() => {
      emitWs(mock.videos, {
        topic: `job:${job.id}`,
        type: 'completed',
        data: { video_id: v.id },
      });
    });
    expect(await within(card).findByText('Ready')).toBeInTheDocument();
    // The finished video is re-read once (GET /videos/{id}) for its job's coverage.
    expect(await within(card).findByText('64% wide')).toBeInTheDocument();
    expect(mock.videos.log).toContain(`GET /api/v1/videos/${v.id}`);
    // Finished jobs are unsubscribed.
    await waitFor(() => expect(wsTopics(mock.videos)).toEqual([]));
  });

  it('marks the card failed when the job fails', async () => {
    const { v, j } = seed('Training', { status: 'inferring', progress: 10 });
    const job = j ?? mockJob(v);
    renderApp('/library');
    const card = await screen.findByRole('article', { name: 'Training' });
    await waitFor(() => expect(wsTopics(mock.videos)).toEqual([`job:${job.id}`]));
    act(() => {
      emitWs(mock.videos, {
        topic: `job:${job.id}`,
        type: 'failed',
        data: { code: 'NO_WIDE_SHOTS', retryable: false },
      });
    });
    expect(await within(card).findByText('Failed')).toBeInTheDocument();
    expect(
      within(card).getByText(/No wide broadcast shots were found in this video/),
    ).toBeInTheDocument();
  });

  it.each([
    ['viewer', ['Open']],
    ['editor', ['Open', 'Rename', 'Retry analysis', 'Delete']],
    ['owner', ['Open', 'Rename', 'Retry analysis', 'Delete']],
  ] as const)('⋮ menu for a failed video as %s', async (role, items) => {
    seed('Cup tie', {
      status: 'failed',
      error: { code: 'NO_WIDE_SHOTS', retryable: true, detail: null },
    });
    renderApp('/library', { auth: testSession({ role }) });
    const { menu } = await openMenu('Cup tie');
    expect(
      within(menu)
        .getAllByRole('menuitem')
        .map((i) => i.textContent),
    ).toEqual(items);
  });

  it('viewer: no ⋮ menu on an uploading video (nothing to do)', async () => {
    mock.videos.videos.push(mockVideo({ title: 'raw.mp4', status: 'uploading' }));
    renderApp('/library', { auth: testSession({ role: 'viewer' }) });
    const card = await screen.findByRole('article', { name: 'raw.mp4' });
    expect(within(card).getByText('Uploading')).toBeInTheDocument();
    expect(within(card).queryByRole('button')).not.toBeInTheDocument();
    expect(within(card).queryByRole('link')).not.toBeInTheDocument();
  });

  it('DLG-04 renames optimistically and PATCHes the title', async () => {
    const { v } = seed('Untitled 1', DONE);
    renderApp('/library');
    const { user, menu } = await openMenu('Untitled 1');
    await user.click(within(menu).getByRole('menuitem', { name: 'Rename' }));
    const dialog = await screen.findByRole('dialog', { name: 'Rename' });
    const input = within(dialog).getByRole('textbox', { name: 'Title' });
    await user.clear(input);
    await user.click(within(dialog).getByRole('button', { name: 'Save' }));
    expect(within(dialog).getByText('Enter a title.')).toBeInTheDocument();

    await user.type(input, 'Cup final');
    await user.click(within(dialog).getByRole('button', { name: 'Save' }));
    await waitFor(() => expect(screen.queryByRole('dialog')).not.toBeInTheDocument());
    expect(await screen.findByRole('article', { name: 'Cup final' })).toBeInTheDocument();
    await waitFor(() =>
      expect(mock.videos.log).toContain(`PATCH /api/v1/videos/${v.id} {"title":"Cup final"}`),
    );
  });

  it('DLG-01 deletes optimistically; Undo restores and nothing is sent', async () => {
    seed('Keep me', DONE);
    seed('Other', DONE);
    renderApp('/library');
    const { user, menu } = await openMenu('Keep me');
    await user.click(within(menu).getByRole('menuitem', { name: 'Delete' }));
    const dialog = await screen.findByRole('dialog', { name: 'Delete Keep me?' });
    expect(
      within(dialog).getByText(
        "Video, analysis, exports and share links will be removed. This can't be undone after 10 seconds.",
      ),
    ).toBeInTheDocument();
    await user.click(within(dialog).getByRole('button', { name: 'Delete' }));

    await waitFor(() =>
      expect(screen.queryByRole('article', { name: 'Keep me' })).not.toBeInTheDocument(),
    );
    expect(screen.getByText('“Keep me” deleted.')).toBeInTheDocument();
    await user.click(screen.getByRole('button', { name: 'Undo' }));
    expect(await screen.findByRole('article', { name: 'Keep me' })).toBeInTheDocument();
    expect(mock.videos.log.some((l) => l.startsWith('DELETE'))).toBe(false);
  });

  it('retries a failed analysis', async () => {
    const { v, j } = seed('Retry me', {
      status: 'failed',
      progress: 37,
      error: { code: 'HOMOGRAPHY_UNAVAILABLE', retryable: true, detail: null },
    });
    const job = j ?? mockJob(v);
    renderApp('/library');
    const { user, menu, card } = await openMenu('Retry me');
    await user.click(within(menu).getByRole('menuitem', { name: 'Retry analysis' }));
    await waitFor(() => expect(mock.videos.log).toContain(`POST /api/v1/jobs/${job.id}/retry`));
    expect(await within(card).findByText('Queued')).toBeInTheDocument();
    expect(await screen.findByText('Analysis restarted.')).toBeInTheDocument();
  });

  it('filters by status and searches titles client-side (URL-backed)', async () => {
    seed('Riverside vs Harbor', DONE);
    seed('Équipe B training', {
      status: 'failed',
      error: { code: 'NO_WIDE_SHOTS', retryable: false, detail: null },
    });
    seed('Riverside vs Ajax', { status: 'inferring', progress: 5 });
    const user = userEvent.setup();
    const { router } = renderApp('/library?status=failed');
    await screen.findByRole('article', { name: 'Équipe B training' });
    expect(screen.getAllByRole('article')).toHaveLength(1);

    act(() => void router.navigate({ to: '/library', search: {} }));
    await waitFor(() => expect(screen.getAllByRole('article')).toHaveLength(3));

    // E01: debounced, accent-insensitive, mirrored into ?q=.
    const box = screen.getByRole('searchbox', { name: 'Search matches' });
    await user.type(box, 'equipe');
    await waitFor(() => expect(router.state.location.search).toEqual({ q: 'equipe' }));
    expect(screen.getAllByRole('article')).toHaveLength(1);

    await user.clear(box);
    await user.type(box, 'nothing here');
    expect(await screen.findByText('No matches fit these filters.')).toBeInTheDocument();
    await user.click(screen.getByRole('button', { name: 'Clear filters' }));
    await waitFor(() => expect(screen.getAllByRole('article')).toHaveLength(3));
    expect(box).toHaveValue('');
    expect(router.state.location.search).toEqual({});
  });
});

describe('pending deletes (DLG-01 10 s window)', () => {
  it('sends DELETE when the window ends, not before', async () => {
    applySession(testSession());
    const { v } = seed('Gone');
    const qc = new QueryClient();
    const onDeleted = vi.fn();
    scheduleDelete(qc, v.id, { onDeleted }, 40);
    expect(usePendingDeletes.getState().ids.has(v.id)).toBe(true);
    expect(mock.videos.log).toEqual([]);
    await waitFor(() => expect(onDeleted).toHaveBeenCalled());
    expect(mock.videos.log).toEqual([`DELETE /api/v1/videos/${v.id}`]);
    expect(usePendingDeletes.getState().ids.has(v.id)).toBe(false);
  });

  it('DELETE of a video with a running job cancels it server-side; a failure restores the card', async () => {
    applySession(testSession());
    const { v, j } = seed('Running', { status: 'inferring', progress: 20 });
    const onDeleted = vi.fn();
    scheduleDelete(new QueryClient(), v.id, { onDeleted }, 10);
    await waitFor(() => expect(onDeleted).toHaveBeenCalled());
    expect(j?.status).toBe('cancelled');
    expect(mock.videos.videos.find((x) => x.id === v.id)?.status).toBe('deleted');

    const onError = vi.fn();
    scheduleDelete(new QueryClient(), v.id, { onError }, 10); // already gone → 404 problem
    await waitFor(() => expect(onError).toHaveBeenCalled());
    expect(onError.mock.calls[0]?.[0]).toMatchObject({ code: 'NOT_FOUND' });
    expect(usePendingDeletes.getState().ids.has(v.id)).toBe(false);
  });

  it('undo inside the window cancels the request', async () => {
    applySession(testSession());
    const { v } = seed('Stays');
    const pending = scheduleDelete(new QueryClient(), v.id, {}, 30);
    pending.undo();
    await new Promise((r) => setTimeout(r, 60));
    expect(mock.videos.log).toEqual([]);
    expect(usePendingDeletes.getState().ids.size).toBe(0);
  });
});
