import type { WsControlMessage, WsJobEvent, WsServerEvent } from './types';
import { WsClient, wsEndpoint, type WebSocketLike, type WsStatus } from './ws';

class FakeSocket implements WebSocketLike {
  static all: FakeSocket[] = [];
  readyState = 0;
  sent: unknown[] = [];
  closed: { code?: number; reason?: string } | null = null;
  onopen: ((ev: Event) => void) | null = null;
  onclose: ((ev: CloseEvent) => void) | null = null;
  onmessage: ((ev: MessageEvent) => void) | null = null;
  onerror: ((ev: Event) => void) | null = null;

  constructor(readonly url: string) {
    FakeSocket.all.push(this);
  }
  send(data: string) {
    this.sent.push(JSON.parse(data));
  }
  close(code?: number, reason?: string) {
    this.closed = { code, reason };
    this.readyState = 3;
  }
  // server side
  open() {
    this.readyState = 1;
    this.onopen?.(new Event('open'));
  }
  receive(msg: unknown) {
    this.onmessage?.(new MessageEvent('message', { data: JSON.stringify(msg) }));
  }
  drop(code = 1006) {
    this.readyState = 3;
    this.onclose?.(new CloseEvent('close', { code }));
  }
}

const last = () => {
  const s = FakeSocket.all.at(-1);
  if (!s) throw new Error('no socket');
  return s;
};

const progress = (topic: string, pct: number): WsServerEvent => ({
  topic,
  type: 'progress',
  data: { stage: 'infer', pct },
});

function makeClient(over: Partial<ConstructorParameters<typeof WsClient>[0]> = {}) {
  return new WsClient({
    url: () => 'wss://eu.api.test/api/v1/ws',
    getToken: () => 'tok en',
    createSocket: (url) => new FakeSocket(url),
    random: () => 1, // no jitter: delay = full backoff
    ...over,
  });
}

/** Let the async connect() (token refresh await) settle. */
const flush = () => vi.advanceTimersByTimeAsync(0);

beforeEach(() => {
  FakeSocket.all = [];
  vi.useFakeTimers();
});
afterEach(() => vi.useRealTimers());

describe('WsClient', () => {
  it('connects lazily with the token in the query and subscribes once open', async () => {
    const c = makeClient();
    expect(FakeSocket.all).toHaveLength(0);
    const got: WsServerEvent[] = [];
    c.subscribe('job:aaaa1111', (e) => got.push(e));
    c.subscribe('job:aaaa1111', () => undefined); // second listener: no second subscribe op
    await flush();
    expect(FakeSocket.all).toHaveLength(1);
    expect(last().url).toBe('wss://eu.api.test/api/v1/ws?token=tok%20en');
    expect(c.getStatus()).toBe('connecting');

    last().open();
    expect(c.getStatus()).toBe('open');
    expect(last().sent).toEqual([{ op: 'subscribe', topic: 'job:aaaa1111' }]);

    last().receive(progress('job:aaaa1111', 12));
    last().receive(progress('job:other', 99)); // not subscribed
    last().receive({ type: 'pong' }); // heartbeat frame, not an event
    expect(got).toEqual([progress('job:aaaa1111', 12)]);
  });

  it('one socket per client even when many topics subscribe before it opens', async () => {
    const c = makeClient();
    c.subscribe('job:a1a1a1a1', () => undefined);
    c.subscribe('job:b2b2b2b2', () => undefined);
    await flush();
    expect(FakeSocket.all).toHaveLength(1);
    last().open();
    expect(last().sent).toEqual([
      { op: 'subscribe', topic: 'job:a1a1a1a1' },
      { op: 'subscribe', topic: 'job:b2b2b2b2' },
    ]);
    c.subscribe('job:c3c3c3c3', () => undefined); // already open → sent immediately
    expect(last().sent.at(-1)).toEqual({ op: 'subscribe', topic: 'job:c3c3c3c3' });
  });

  it('reconnects with exponential backoff and re-subscribes every active topic', async () => {
    const statuses: WsStatus[] = [];
    const c = makeClient({ backoff: { baseMs: 500, maxMs: 4000 } });
    c.onStatus((s) => statuses.push(s));
    const off = c.subscribe('job:a1a1a1a1', () => undefined);
    c.subscribe('job:b2b2b2b2', () => undefined);
    await flush();
    last().open();
    off(); // a1 unsubscribed before the drop → not re-subscribed

    last().drop();
    expect(c.getStatus()).toBe('reconnecting');
    await vi.advanceTimersByTimeAsync(499);
    expect(FakeSocket.all).toHaveLength(1);
    await vi.advanceTimersByTimeAsync(1);
    expect(FakeSocket.all).toHaveLength(2);

    // Fails again before opening: next delay doubles.
    last().drop();
    await vi.advanceTimersByTimeAsync(999);
    expect(FakeSocket.all).toHaveLength(2);
    await vi.advanceTimersByTimeAsync(1);
    expect(FakeSocket.all).toHaveLength(3);

    last().open();
    expect(last().sent).toEqual([{ op: 'subscribe', topic: 'job:b2b2b2b2' }]);
    expect(statuses).toEqual(['connecting', 'open', 'reconnecting', 'open']);

    // A successful open resets the backoff.
    last().drop();
    await vi.advanceTimersByTimeAsync(500);
    expect(FakeSocket.all).toHaveLength(4);
  });

  it('caps the backoff and applies jitter', () => {
    const c = makeClient({ backoff: { baseMs: 500, maxMs: 30_000 }, random: () => 0 });
    expect(c.backoffDelay(0)).toBe(250);
    expect(c.backoffDelay(3)).toBe(2000);
    expect(c.backoffDelay(20)).toBe(15_000);
    const full = makeClient({ backoff: { baseMs: 500, maxMs: 30_000 }, random: () => 1 });
    expect(full.backoffDelay(20)).toBe(30_000);
  });

  it('pings every heartbeat and replaces a socket that stays silent', async () => {
    const c = makeClient({ heartbeatMs: 25_000 });
    c.subscribe('job:a1a1a1a1', () => undefined);
    await flush();
    const first = last();
    first.open();
    await vi.advanceTimersByTimeAsync(25_000);
    expect(first.sent.at(-1)).toEqual({ op: 'ping' });

    // Any frame resets the stale timer (2 × 25 s + 5 s).
    await vi.advanceTimersByTimeAsync(20_000);
    first.receive({ type: 'pong' });
    await vi.advanceTimersByTimeAsync(54_000);
    expect(first.closed).toBeNull();

    await vi.advanceTimersByTimeAsync(1_000);
    expect(first.closed).toEqual({ code: 4000, reason: 'heartbeat timeout' });
    expect(c.getStatus()).toBe('reconnecting');
    await vi.advanceTimersByTimeAsync(500);
    expect(FakeSocket.all).toHaveLength(2);
    last().open();
    expect(last().sent).toEqual([{ op: 'subscribe', topic: 'job:a1a1a1a1' }]);
  });

  it('refreshes the token before reconnecting after an auth close', async () => {
    let token = 'old';
    const refreshToken = vi.fn(() => {
      token = 'new';
      return Promise.resolve(token);
    });
    const c = makeClient({ getToken: () => token, refreshToken });
    c.subscribe('job:a1a1a1a1', () => undefined);
    await flush();
    last().open();
    last().drop(4401);
    await vi.advanceTimersByTimeAsync(500);
    expect(refreshToken).toHaveBeenCalledTimes(1);
    expect(last().url).toContain('token=new');
  });

  it('stops on 4403 (forbidden / wrong region) but retries 1013 with backoff', async () => {
    const c = makeClient();
    c.subscribe('job:a1a1a1a1', () => undefined);
    await flush();
    last().open();
    last().drop(1013);
    expect(c.getStatus()).toBe('reconnecting');
    await vi.advanceTimersByTimeAsync(500);
    last().open();
    last().drop(4403);
    expect(c.getStatus()).toBe('idle');
    await vi.advanceTimersByTimeAsync(60_000);
    expect(FakeSocket.all).toHaveLength(2);
  });

  it('ignores control frames (subscribed / error / ping) but treats them as liveness', async () => {
    const got: WsServerEvent[] = [];
    const c = makeClient();
    c.subscribe('job:a1a1a1a1', (e) => got.push(e));
    await flush();
    last().open();
    last().receive({ type: 'subscribed', topic: 'job:a1a1a1a1', data: { status: 'inferring' } });
    last().receive({ type: 'error', topic: 'job:a1a1a1a1', data: { code: 'NOT_FOUND' } });
    await vi.advanceTimersByTimeAsync(50_000);
    last().receive({ type: 'ping' });
    await vi.advanceTimersByTimeAsync(50_000);
    expect(last().closed).toBeNull();
    last().receive(progress('job:a1a1a1a1', 50)); // snapshot after subscribed
    expect(got).toEqual([progress('job:a1a1a1a1', 50)]);
  });

  it('routes `cancelled` to the topic and typed control messages to onControl', async () => {
    const got: WsJobEvent[] = [];
    const control: WsControlMessage[] = [];
    const c = makeClient();
    c.subscribe('job:a1a1a1a1', (e) => got.push(e));
    const off = c.onControl((m) => control.push(m));
    await flush();
    last().open();
    const frames: WsControlMessage[] = [
      { type: 'subscribed', topic: 'job:a1a1a1a1', data: { status: 'inferring' } },
      {
        type: 'error',
        topic: 'job:b2b2b2b2',
        data: { code: 'NOT_FOUND', detail: 'job not found' },
      },
      { type: 'ping', ts: 1_791_000_000 },
      { type: 'pong' },
      { type: 'unsubscribed', topic: 'job:b2b2b2b2' },
    ];
    for (const f of frames) last().receive(f);
    const cancelled: WsJobEvent = {
      topic: 'job:a1a1a1a1',
      type: 'cancelled',
      data: { video_id: 'v1' },
    };
    last().receive(cancelled);
    last().receive({ type: 'exploded', topic: 'job:a1a1a1a1', data: {} }); // unknown: dropped
    last().receive({ type: 'progress', topic: 'job:a1a1a1a1' }); // malformed job event: dropped
    expect(got).toEqual([cancelled]);
    expect(control).toEqual(frames);
    off();
    last().receive({ type: 'pong' });
    expect(control).toHaveLength(frames.length);
  });

  it('does not connect without a token', async () => {
    const c = makeClient({ getToken: () => null, refreshToken: () => Promise.resolve(null) });
    c.subscribe('job:a1a1a1a1', () => undefined);
    await flush();
    expect(FakeSocket.all).toHaveLength(0);
    expect(c.getStatus()).toBe('idle');
  });

  it('unsubscribes the last listener and closes the idle socket', async () => {
    const c = makeClient({ idleCloseMs: 5_000 });
    const off = c.subscribe('job:a1a1a1a1', () => undefined);
    await flush();
    last().open();
    off();
    expect(last().sent.at(-1)).toEqual({ op: 'unsubscribe', topic: 'job:a1a1a1a1' });
    expect(c.activeTopics()).toEqual([]);
    await vi.advanceTimersByTimeAsync(4_999);
    expect(last().closed).toBeNull();
    await vi.advanceTimersByTimeAsync(1);
    expect(last().closed?.code).toBe(1000);
    expect(c.getStatus()).toBe('idle');
  });

  it('a re-subscribe within the idle window keeps the socket', async () => {
    const c = makeClient({ idleCloseMs: 5_000 });
    const off = c.subscribe('job:a1a1a1a1', () => undefined);
    await flush();
    last().open();
    off();
    c.subscribe('job:a1a1a1a1', () => undefined);
    await vi.advanceTimersByTimeAsync(10_000);
    expect(FakeSocket.all).toHaveLength(1);
    expect(last().closed).toBeNull();
  });
});

describe('wsEndpoint', () => {
  it('maps http(s) to ws(s)', () => {
    expect(wsEndpoint('https://eu.api.pitchvision.ai')).toBe(
      'wss://eu.api.pitchvision.ai/api/v1/ws',
    );
    expect(wsEndpoint('http://localhost:8000')).toBe('ws://localhost:8000/api/v1/ws');
  });
});
