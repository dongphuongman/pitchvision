import { fireEvent, screen, waitFor, within } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { http, HttpResponse } from 'msw';
import { mockJob, mockVideo, type MockJob } from '@/mocks/videos';
import { setupIdentityMock } from '@/test/msw';
import { renderApp, testSession } from '@/test/renderApp';

const mock = setupIdentityMock();

function seed(
  job: Partial<MockJob> = { status: 'done', progress: 100, pitch_calibration: 'degraded' },
) {
  const v = mockVideo({ title: 'France vs Switzerland', status: 'ready', duration_ms: 120_000 });
  mock.videos.videos.push(v);
  mock.videos.jobs.push(mockJob(v, job));
  return v;
}

function logged(prefix: string): unknown[] {
  return mock.videos.log
    .filter((l) => l.startsWith(prefix))
    .map((l) => JSON.parse(l.slice(l.indexOf('{'))) as unknown);
}

/** The stage is shown at half size (640 × 360) → client px × 2 = video px. */
async function stage() {
  const el = await screen.findByTestId('calibration-stage');
  el.getBoundingClientRect = () =>
    ({ left: 0, top: 0, width: 640, height: 360, right: 640, bottom: 360, x: 0, y: 0 }) as DOMRect;
  await waitFor(() => expect(el.querySelector('img')).not.toBeNull());
  return el;
}

type User = ReturnType<typeof userEvent.setup>;

async function placePoint(user: User, el: HTMLElement, landmark: string, x: number, y: number) {
  await user.click(screen.getByRole('radio', { name: landmark }));
  fireEvent.click(el, { clientX: x / 2, clientY: y / 2 });
}

/** The right penalty box: a well-spread quad. */
async function placeBox(user: User, el: HTMLElement) {
  await placePoint(user, el, 'Right penalty box corner (top)', 800, 200);
  await placePoint(user, el, 'Right penalty box × goal line (top)', 1200, 200);
  await placePoint(user, el, 'Right penalty box × goal line (bottom)', 1240, 600);
  await placePoint(user, el, 'Right penalty box corner (bottom)', 760, 600);
}

describe('SCR-15 Manual pitch calibration', () => {
  it('places 4 points, picks the scope and submits (POST) → processing', async () => {
    const v = seed();
    const user = userEvent.setup();
    const { router } = renderApp(`/videos/${v.id}/calibrate?t=10`, { auth: testSession() });
    expect(await screen.findByText('Pitch mapping: partly low')).toBeInTheDocument();
    const el = await stage();
    expect(screen.getByText('Add at least 4 points (0/4).')).toBeInTheDocument();

    await placeBox(user, el);
    expect(screen.getByTestId('calibration-hint')).toHaveTextContent('4 points — ready to apply.');
    expect(screen.getByRole('heading', { name: 'Points (4/8)' })).toBeInTheDocument();
    expect(
      screen.getByRole('radio', { name: 'Right penalty box corner (top) (point 1)' }),
    ).toBeInTheDocument();
    expect(screen.getByTestId('pitch-preview')).toBeInTheDocument();

    await user.click(screen.getByRole('radio', { name: 'Whole video' }));
    expect(screen.getByText(/cameras pan and zoom/)).toBeInTheDocument();
    await user.click(screen.getByRole('button', { name: 'Apply & recompute' }));

    await waitFor(() => expect(logged('POST calibration')).toHaveLength(1));
    expect(logged('POST calibration')[0]).toEqual({
      points: [
        { template_index: 17, x: 800, y: 200, frame_ms: 10_000 },
        { template_index: 25, x: 1200, y: 200, frame_ms: 10_000 },
        { template_index: 28, x: 1240, y: 600, frame_ms: 10_000 },
        { template_index: 20, x: 760, y: 600, frame_ms: 10_000 },
      ],
      scope: 'video',
    });
    await waitFor(() => expect(router.state.location.pathname).toBe(`/videos/${v.id}/processing`));
    expect(mock.videos.jobs.some((j) => j.kind === 'recalibrate')).toBe(true);
  });

  it('warns about (nearly) collinear points and keeps Apply disabled', async () => {
    const v = seed();
    const user = userEvent.setup();
    renderApp(`/videos/${v.id}/calibrate?t=10`, { auth: testSession() });
    const el = await stage();
    await placePoint(user, el, 'Corner (top-left)', 100, 300);
    await placePoint(user, el, 'Corner (top-right)', 400, 302);
    await placePoint(user, el, 'Corner (bottom-left)', 700, 304);
    await placePoint(user, el, 'Corner (bottom-right)', 1000, 306);
    expect(screen.getByTestId('calibration-hint')).toHaveTextContent(/nearly on one straight line/);
    expect(screen.getByRole('button', { name: 'Apply & recompute' })).toBeDisabled();

    // Removing a point brings back the "add more" hint.
    await user.click(
      screen.getByRole('button', { name: 'Remove point 4 (Corner (bottom-right))' }),
    );
    expect(screen.getByTestId('calibration-hint')).toHaveTextContent(
      'Add at least 4 points (3/4).',
    );
  });

  it('shows the server 422 reason inline', async () => {
    const v = seed();
    mock.server.use(
      http.post('*/api/v1/results/:id/calibration', () =>
        HttpResponse.json(
          {
            type: 'about:blank',
            title: 'Points are collinear',
            status: 422,
            code: 'VALIDATION_FAILED',
            reason: 'collinear',
          },
          { status: 422, headers: { 'Content-Type': 'application/problem+json' } },
        ),
      ),
    );
    const user = userEvent.setup();
    renderApp(`/videos/${v.id}/calibrate?t=10`, { auth: testSession() });
    const el = await stage();
    await placeBox(user, el);
    await user.click(screen.getByRole('button', { name: 'Apply & recompute' }));
    expect(await screen.findByRole('alert')).toHaveTextContent(
      "The points are (nearly) on one straight line, so the pitch couldn't be mapped.",
    );
  });

  it('places and nudges a point with the keyboard', async () => {
    const v = seed();
    const user = userEvent.setup();
    renderApp(`/videos/${v.id}/calibrate?t=10`, { auth: testSession() });
    const el = await stage();
    const diagram = screen.getByRole('radiogroup', { name: 'Pitch landmarks' });
    within(diagram).getByRole('radio', { name: 'Corner (top-left)' }).focus();
    await user.keyboard('{ArrowRight}');
    expect(
      within(diagram).getByRole('radio', { name: 'Left penalty box × goal line (top)' }),
    ).toHaveAttribute('aria-checked', 'true');
    el.focus();
    await user.keyboard('{Enter}');
    expect(
      screen.getByRole('button', { name: /Point 1: Left penalty box × goal line \(top\)/ }),
    ).toHaveTextContent('640, 360');
    await user.keyboard('{ArrowRight}{ArrowRight}{Shift>}{ArrowDown}{/Shift}');
    expect(screen.getByRole('button', { name: /Point 1/ })).toHaveTextContent('642, 370');
  });

  it('defaults to the middle of the longest wide shot, steps between shots and clears points', async () => {
    const v = seed();
    const user = userEvent.setup();
    renderApp(`/videos/${v.id}/calibrate`, { auth: testSession() });
    // Mock shots: main_wide 57–94 s is the longest → 75.5 s (snapped to 75.52 s).
    expect(await screen.findByText(/^1:15 · frame 1888$/)).toBeInTheDocument();
    const el = await stage();
    await placePoint(user, el, 'Corner (top-left)', 100, 100);
    await user.click(screen.getByRole('button', { name: 'Next wide shot' }));
    expect(await screen.findByText(/^1:48 · frame 2700$/)).toBeInTheDocument();
    expect(screen.getByText('Points cleared because the frame changed.')).toBeInTheDocument();
    expect(screen.getByText('No points yet.')).toBeInTheDocument();
  });

  it('offers the last manual calibration', async () => {
    const v = seed();
    mock.videos.calibrations.set(v.id, {
      correction_id: '00000000-0000-4000-c000-000000000001',
      revision: 2,
      scope: 'shot',
      frame_ms: 12_000,
      created_at: '2026-10-05T10:00:00Z',
      points: [
        { template_index: 17, x: 800, y: 200, frame_ms: 12_000 },
        { template_index: 25, x: 1200, y: 200, frame_ms: 12_000 },
        { template_index: 28, x: 1240, y: 600, frame_ms: 12_000 },
        { template_index: 20, x: 760, y: 600, frame_ms: 12_000 },
      ],
    });
    const user = userEvent.setup();
    renderApp(`/videos/${v.id}/calibrate?t=30`, { auth: testSession() });
    expect(
      await screen.findByText(/Last manual calibration: 4 points at 0:12/),
    ).toBeInTheDocument();
    await user.click(screen.getByRole('button', { name: 'Use these points' }));
    expect(await screen.findByText(/^0:12 · frame 300$/)).toBeInTheDocument();
    expect(screen.getByTestId('calibration-hint')).toHaveTextContent('4 points — ready to apply.');
  });

  it('is read-only for viewers', async () => {
    const v = seed();
    renderApp(`/videos/${v.id}/calibrate?t=10`, { auth: testSession({ role: 'viewer' }) });
    expect(await screen.findByText('Only editors can calibrate the pitch.')).toBeInTheDocument();
    expect(screen.queryByRole('button', { name: 'Apply & recompute' })).not.toBeInTheDocument();
  });
});
