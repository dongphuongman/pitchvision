import { fireEvent, render, screen } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import type { Shot } from '@/api/types';
import { nextLiveActionMs, shotAt, timelineSegments } from './shots';
import { ShotTimeline } from './ShotTimeline';

const shots: Shot[] = [
  { t0_ms: 0, t1_ms: 40_000, type: 'main_wide', conf: 0.9 },
  { t0_ms: 40_000, t1_ms: 50_000, type: 'replay', conf: 0.8 },
  { t0_ms: 50_000, t1_ms: 60_000, type: 'replay', conf: 0.8 },
  { t0_ms: 60_000, t1_ms: 90_000, type: 'main_wide', conf: 0.9 },
  { t0_ms: 90_000, t1_ms: 100_000, type: 'close_up', conf: 0.7 },
];

describe('shot helpers', () => {
  it('finds the shot at a time (half-open)', () => {
    expect(shotAt(shots, 0)?.type).toBe('main_wide');
    expect(shotAt(shots, 39_999)?.type).toBe('main_wide');
    expect(shotAt(shots, 40_000)?.type).toBe('replay');
    expect(shotAt(shots, 99_999)?.type).toBe('close_up');
    expect(shotAt(shots, 100_000)).toBeUndefined();
  });

  it('builds percentage segments, merging same-type neighbours', () => {
    expect(timelineSegments(shots, 100_000)).toEqual([
      { type: 'main_wide', t0: 0, t1: 40_000, left: 0, width: 40 },
      { type: 'replay', t0: 40_000, t1: 60_000, left: 40, width: 20 },
      { type: 'main_wide', t0: 60_000, t1: 90_000, left: 60, width: 30 },
      { type: 'close_up', t0: 90_000, t1: 100_000, left: 90, width: 10 },
    ]);
  });

  it('next live action = next main_wide start, only from a non-live shot', () => {
    expect(nextLiveActionMs(shots, 45_000)).toBe(60_000);
    expect(nextLiveActionMs(shots, 10_000)).toBeNull();
    expect(nextLiveActionMs(shots, 95_000)).toBeNull(); // nothing live after the close-up
  });
});

describe('<ShotTimeline>', () => {
  function setup(currentMs: number) {
    const onSeek = vi.fn();
    render(
      <ShotTimeline shots={shots} durationMs={100_000} currentMs={currentMs} onSeek={onSeek} />,
    );
    const slider = screen.getByRole('slider', { name: 'Timeline' });
    vi.spyOn(slider, 'getBoundingClientRect').mockReturnValue({
      left: 100,
      width: 1000,
      top: 0,
      height: 28,
      right: 1100,
      bottom: 28,
      x: 100,
      y: 0,
      toJSON: () => ({}),
    });
    return { onSeek, slider };
  }

  it('colours segments by shot type and labels the non-live ones', () => {
    setup(10_000);
    const segs = screen.getAllByTestId('shot-segment');
    expect(segs.map((s) => s.dataset.type)).toEqual([
      'main_wide',
      'replay',
      'main_wide',
      'close_up',
    ]);
    expect(segs[1]).toHaveStyle({ left: '40%', width: '20%' });
    expect(segs[1]).toHaveTextContent('Replay');
    expect(segs[3]).toHaveTextContent('Close-up');
    expect(segs[0]).toHaveTextContent('');
    expect(screen.getByTestId('playhead')).toHaveStyle({ left: '10%' });
  });

  it('click seeks to the clicked position', () => {
    const { onSeek, slider } = setup(0);
    fireEvent.pointerDown(slider, { clientX: 350, button: 0 });
    expect(onSeek).toHaveBeenCalledWith(25_000);
    fireEvent.pointerDown(slider, { clientX: 5000, button: 0 });
    expect(onSeek).toHaveBeenLastCalledWith(100_000);
  });

  it('arrow keys seek ±5 s', async () => {
    const { onSeek, slider } = setup(20_000);
    slider.focus();
    await userEvent.keyboard('{ArrowRight}');
    expect(onSeek).toHaveBeenLastCalledWith(25_000);
    await userEvent.keyboard('{ArrowLeft}');
    expect(onSeek).toHaveBeenLastCalledWith(15_000);
    await userEvent.keyboard('{Home}');
    expect(onSeek).toHaveBeenLastCalledWith(0);
  });

  it('offers "Skip to next live action" during a replay', async () => {
    const { onSeek } = setup(45_000);
    expect(screen.getByText('Replay — no tracking data')).toBeInTheDocument();
    await userEvent.click(screen.getByRole('button', { name: 'Skip to next live action' }));
    expect(onSeek).toHaveBeenCalledWith(60_000);
  });

  it('hides the skip button during live play', () => {
    setup(10_000);
    expect(screen.queryByRole('button', { name: 'Skip to next live action' })).toBeNull();
  });
});
