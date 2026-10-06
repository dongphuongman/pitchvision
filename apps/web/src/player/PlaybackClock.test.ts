import {
  frameForMs,
  frameFromCurrentTime,
  frameFromMediaTime,
  framesPerTile,
  tileForFrame,
  tileUrl,
  timeForFrame,
} from './frames';
import { PlaybackClock, type ClockState, type FrameScheduler } from './PlaybackClock';

describe('frame math (25 fps)', () => {
  it('rVFC mediaTime → nearest frame, absorbing float jitter', () => {
    expect(frameFromMediaTime(0, 25)).toBe(0);
    expect(frameFromMediaTime(0.04, 25)).toBe(1);
    expect(frameFromMediaTime(0.12, 25)).toBe(3); // 0.12 × 25 = 3.0000000000000004
    expect(frameFromMediaTime(0.119999, 25)).toBe(3);
    expect(frameFromMediaTime(49.6 - 1e-9, 25)).toBe(1240);
    expect(frameFromMediaTime(3600, 25)).toBe(90_000);
  });

  it('rounds at the half-frame boundary', () => {
    expect(frameFromMediaTime(0.0199, 25)).toBe(0);
    expect(frameFromMediaTime(0.02, 25)).toBe(1); // exactly half → up
    expect(frameFromMediaTime(0.0601, 25)).toBe(2);
  });

  it('currentTime (fallback) floors within the frame interval', () => {
    expect(frameFromCurrentTime(0.0399, 25)).toBe(0);
    expect(frameFromCurrentTime(0.04, 25)).toBe(1);
    expect(frameFromCurrentTime(0.12, 25)).toBe(3);
    expect(frameFromCurrentTime(-1, 25)).toBe(0);
  });

  it('seek target is mid-frame and round-trips', () => {
    expect(timeForFrame(0, 25)).toBeCloseTo(0.02);
    expect(timeForFrame(1240, 25)).toBeCloseTo(49.62);
    for (const f of [0, 1, 24, 25, 1239, 141_299]) {
      expect(frameFromCurrentTime(timeForFrame(f, 25), 25)).toBe(f);
      expect(frameFromMediaTime(f / 25, 25)).toBe(f);
    }
  });

  it('tile math: 10 s tiles hold 250 frames, URLs are 6-digit padded', () => {
    const fpt = framesPerTile(10_000, 25);
    expect(fpt).toBe(250);
    expect(tileForFrame(249, fpt)).toBe(0);
    expect(tileForFrame(250, fpt)).toBe(1);
    expect(frameForMs(10_000, 25)).toBe(250);
    expect(tileUrl('https://t/x/{tile_no}.json.gz?sig=1', 42)).toBe(
      'https://t/x/000042.json.gz?sig=1',
    );
  });
});

type FrameCb = (now: number, meta: { mediaTime: number }) => void;

class FakeVideo {
  currentTime = 0;
  paused = true;
  playbackRate = 1;
  private cbs = new Map<number, FrameCb>();
  private seq = 0;
  private listeners = new Map<string, Set<() => void>>();
  requestVideoFrameCallback?: (cb: FrameCb) => number = (cb) => {
    this.seq += 1;
    this.cbs.set(this.seq, cb);
    return this.seq;
  };
  cancelVideoFrameCallback = (h: number) => {
    this.cbs.delete(h);
  };
  pause() {
    this.paused = true;
    this.emit('pause');
  }
  addEventListener(type: string, l: () => void) {
    let set = this.listeners.get(type);
    if (!set) {
      set = new Set();
      this.listeners.set(type, set);
    }
    set.add(l);
  }
  removeEventListener(type: string, l: () => void) {
    this.listeners.get(type)?.delete(l);
  }
  emit(type: string) {
    for (const l of this.listeners.get(type) ?? []) (l as (e: Event) => void)(new Event(type));
  }
  /** Present a frame (what the compositor does): fires pending rVFC callbacks once. */
  present(mediaTime: number) {
    const cbs = [...this.cbs.values()];
    this.cbs.clear();
    for (const cb of cbs) cb(performance.now(), { mediaTime });
  }
  pendingCallbacks() {
    return this.cbs.size;
  }
}

function manualScheduler() {
  let queue: (() => void)[] = [];
  const s: FrameScheduler & { tick: () => void } = {
    request: (cb) => queue.push(cb),
    cancel: () => {
      queue = [];
    },
    tick: () => {
      const q = queue;
      queue = [];
      q.forEach((cb) => cb());
    },
  };
  return s;
}

describe('PlaybackClock (rVFC)', () => {
  it('reports the exact frame of every presented frame', () => {
    const video = new FakeVideo();
    const clock = new PlaybackClock(video, { fps: 25 });
    const seen: ClockState[] = [];
    clock.subscribe((s) => seen.push(s));
    clock.start();
    expect(clock.approximate).toBe(false);
    video.present(1.2);
    video.present(1.24 - 1e-7);
    video.present(1.24 - 1e-7); // same frame again: no event
    expect(seen.map((s) => s.frameIdx)).toEqual([30, 31]);
    expect(clock.getState()).toMatchObject({ frameIdx: 31, approximate: false });
    expect(video.pendingCallbacks()).toBe(1); // re-armed
    clock.stop();
    expect(video.pendingCallbacks()).toBe(0);
  });

  it('tracks play / pause / rate changes', () => {
    const video = new FakeVideo();
    const clock = new PlaybackClock(video);
    clock.start();
    video.paused = false;
    video.emit('play');
    expect(clock.getState().playing).toBe(true);
    clock.setRate(2);
    video.emit('ratechange');
    expect(clock.getState().rate).toBe(2);
    // Frame math doesn't depend on the rate: mediaTime is still the PTS.
    video.present(10);
    expect(clock.getState().frameIdx).toBe(250);
    video.pause();
    expect(clock.getState().playing).toBe(false);
  });

  it('announces seeks with the target frame and seeks to mid-frame', () => {
    const video = new FakeVideo();
    const clock = new PlaybackClock(video);
    const seeks: number[] = [];
    clock.onSeek((f) => seeks.push(f));
    clock.start();
    clock.seekToFrame(1240);
    expect(video.currentTime).toBeCloseTo(49.62);
    video.emit('seeking');
    expect(seeks).toEqual([1240]);
    expect(clock.getState().seeking).toBe(true);
    video.emit('seeked');
    video.present(49.6);
    expect(clock.getState()).toMatchObject({ seeking: false, frameIdx: 1240 });

    clock.seekToMs(10_000);
    video.emit('seeking');
    expect(seeks.at(-1)).toBe(250);
  });

  it('steps whole frames and pauses', () => {
    const video = new FakeVideo();
    video.paused = false;
    const clock = new PlaybackClock(video);
    clock.start();
    video.present(4); // frame 100
    clock.step(1);
    expect(video.paused).toBe(true);
    expect(video.currentTime).toBeCloseTo(101.5 / 25);
    clock.step(-200);
    expect(video.currentTime).toBeCloseTo(0.5 / 25);
  });
});

describe('PlaybackClock (fallback without rVFC)', () => {
  it('samples currentTime on animation frames and flags approximate sync', () => {
    const video = new FakeVideo();
    video.requestVideoFrameCallback = undefined;
    const sched = manualScheduler();
    const clock = new PlaybackClock(video, { fps: 25, scheduler: sched });
    const frames: number[] = [];
    clock.subscribe((s) => frames.push(s.frameIdx));
    clock.start();
    expect(clock.getState().approximate).toBe(true);
    video.currentTime = 0.05;
    sched.tick();
    sched.tick(); // same frame: no event
    video.currentTime = 0.09;
    sched.tick();
    expect(frames).toEqual([1, 2]);
    clock.stop();
    video.currentTime = 1;
    sched.tick();
    expect(frames).toEqual([1, 2]);
  });

  it('reads the frame right after a seek', () => {
    const video = new FakeVideo();
    video.requestVideoFrameCallback = undefined;
    const clock = new PlaybackClock(video, { scheduler: manualScheduler() });
    clock.start();
    clock.seekToFrame(500);
    video.emit('seeking');
    video.emit('seeked');
    expect(clock.getState().frameIdx).toBe(500);
  });
});
