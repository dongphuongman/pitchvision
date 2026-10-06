import {
  DEFAULT_FPS,
  frameForMs,
  frameFromCurrentTime,
  frameFromMediaTime,
  timeForFrame,
} from './frames';

/** Subset of HTMLVideoElement the clock needs (tests pass a fake). */
export interface VideoLike {
  currentTime: number;
  readonly paused: boolean;
  playbackRate: number;
  requestVideoFrameCallback?: (
    cb: (now: number, metadata: { mediaTime: number; presentedFrames?: number }) => void,
  ) => number;
  cancelVideoFrameCallback?: (handle: number) => void;
  pause(): void;
  play?(): Promise<void>;
  addEventListener(type: string, listener: () => void): void;
  removeEventListener(type: string, listener: () => void): void;
}

export interface FrameScheduler {
  request(cb: () => void): number;
  cancel(handle: number): void;
}

const rafScheduler: FrameScheduler = {
  request: (cb) => requestAnimationFrame(cb),
  cancel: (h) => cancelAnimationFrame(h),
};

export interface ClockState {
  /** Frame index on the normalized timeline (exact with rVFC). */
  frameIdx: number;
  /** Seconds: rVFC mediaTime, or currentTime in approximate mode. */
  mediaTime: number;
  playing: boolean;
  rate: number;
  seeking: boolean;
  /** True without requestVideoFrameCallback (rAF + currentTime, may be 1–4 frames off). */
  approximate: boolean;
}

export interface PlaybackClockOptions {
  fps?: number;
  scheduler?: FrameScheduler;
  /** Force the rAF fallback even if rVFC exists (debugging). */
  forceApproximate?: boolean;
}

const MEDIA_EVENTS = ['play', 'playing', 'pause', 'ratechange', 'seeking', 'seeked', 'loadeddata'];

/**
 * PlaybackClock (AD §11.1): turns the `<video>` presentation into an exact frame index.
 * With rVFC every presented frame (playback, seek while paused) reports its `mediaTime`;
 * without it, a rAF loop samples `currentTime` and flags the state `approximate`.
 */
export class PlaybackClock {
  readonly fps: number;
  readonly approximate: boolean;
  private readonly video: VideoLike;
  private readonly scheduler: FrameScheduler;
  private state: ClockState;
  private readonly listeners = new Set<(s: ClockState) => void>();
  private readonly seekListeners = new Set<(frameIdx: number) => void>();
  private handle: number | null = null;
  private running = false;

  constructor(video: VideoLike, opts: PlaybackClockOptions = {}) {
    this.video = video;
    this.fps = opts.fps ?? DEFAULT_FPS;
    this.scheduler = opts.scheduler ?? rafScheduler;
    this.approximate =
      !!opts.forceApproximate || typeof video.requestVideoFrameCallback !== 'function';
    const t = video.currentTime;
    this.state = {
      frameIdx: frameFromCurrentTime(t, this.fps),
      mediaTime: t,
      playing: !video.paused,
      rate: video.playbackRate,
      seeking: false,
      approximate: this.approximate,
    };
  }

  getState(): ClockState {
    return this.state;
  }

  /** Called on every state change (new frame, play/pause, rate, seeking). */
  subscribe(listener: (s: ClockState) => void): () => void {
    this.listeners.add(listener);
    return () => this.listeners.delete(listener);
  }

  /** Called when a seek starts, with the target frame (TileStore cancels stale prefetch). */
  onSeek(listener: (frameIdx: number) => void): () => void {
    this.seekListeners.add(listener);
    return () => this.seekListeners.delete(listener);
  }

  start(): void {
    if (this.running) return;
    this.running = true;
    for (const e of MEDIA_EVENTS) this.video.addEventListener(e, this.onMediaEvent);
    this.schedule();
  }

  stop(): void {
    if (!this.running) return;
    this.running = false;
    for (const e of MEDIA_EVENTS) this.video.removeEventListener(e, this.onMediaEvent);
    if (this.handle !== null) {
      if (this.approximate) this.scheduler.cancel(this.handle);
      else this.video.cancelVideoFrameCallback?.(this.handle);
    }
    this.handle = null;
  }

  setRate(rate: number): void {
    this.video.playbackRate = rate;
  }

  togglePlay(): void {
    if (this.video.paused) void this.video.play?.();
    else this.video.pause();
  }

  seekToFrame(f: number): void {
    this.video.currentTime = timeForFrame(f, this.fps);
  }

  seekToMs(ms: number): void {
    this.seekToFrame(frameForMs(ms, this.fps));
  }

  /** ⏮ ⏭ (UIUX SCR-20 E09): pause and move by whole frames. */
  step(frames: number): void {
    if (!this.video.paused) this.video.pause();
    this.seekToFrame(Math.max(0, this.state.frameIdx + frames));
  }

  /* -------------------------------------------------------------------- internals */

  private schedule(): void {
    if (!this.running) return;
    if (this.approximate) {
      this.handle = this.scheduler.request(this.onTick);
    } else {
      const rvfc = this.video.requestVideoFrameCallback;
      if (rvfc) this.handle = rvfc.call(this.video, this.onVideoFrame);
    }
  }

  private readonly onVideoFrame = (_now: number, meta: { mediaTime: number }) => {
    this.update({
      frameIdx: frameFromMediaTime(meta.mediaTime, this.fps),
      mediaTime: meta.mediaTime,
    });
    this.schedule();
  };

  private readonly onTick = () => {
    const t = this.video.currentTime;
    const frameIdx = frameFromCurrentTime(t, this.fps);
    // Only a new frame is news (rAF runs at display rate, not video rate).
    if (frameIdx !== this.state.frameIdx) this.update({ frameIdx, mediaTime: t });
    this.schedule();
  };

  private readonly onMediaEvent = (ev?: Event | string) => {
    const type = typeof ev === 'string' ? ev : ev?.type;
    const v = this.video;
    const patch: Partial<ClockState> = { playing: !v.paused, rate: v.playbackRate };
    if (type === 'seeking') {
      patch.seeking = true;
      const target = frameFromCurrentTime(v.currentTime, this.fps);
      for (const l of this.seekListeners) l(target);
    } else if (type === 'seeked') {
      patch.seeking = false;
      // rVFC follows with the exact frame; the approximate clock reads it now.
      if (this.approximate) patch.frameIdx = frameFromCurrentTime(v.currentTime, this.fps);
    } else if (type === 'loadeddata' && this.state.mediaTime === 0) {
      patch.frameIdx = frameFromCurrentTime(v.currentTime, this.fps);
      patch.mediaTime = v.currentTime;
    }
    this.update(patch);
  };

  private update(patch: Partial<ClockState>): void {
    const s = this.state;
    const changed = (Object.keys(patch) as (keyof ClockState)[]).some((k) => patch[k] !== s[k]);
    if (!changed) return;
    this.state = { ...s, ...patch };
    for (const l of this.listeners) l(this.state);
  }
}
