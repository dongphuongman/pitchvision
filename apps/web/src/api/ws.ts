import { useEffect, useRef, useSyncExternalStore } from 'react';
import { API_BASE_URL } from './client';
import { getAccessToken, refreshAccessToken } from './http';
import type { WsClientOp, WsServerEvent } from './types';

/**
 * WebSocket client (AD §10.3): one socket per tab, `wss://…/api/v1/ws?token=…`, topic
 * subscriptions (`job:{id}`), server heartbeat every 25 s.
 *
 * - Connects lazily on the first subscription; closes after `idleCloseMs` without topics.
 * - Every (re)connect re-sends `subscribe` for all active topics.
 * - Unexpected close → reconnect with exponential backoff + jitter (0.5 s … 30 s).
 * - Heartbeat awareness: the client pings every `heartbeatMs`; if no frame of any kind arrives
 *   for `staleAfterMs` (2 heartbeats + 5 s) the socket is considered dead and replaced.
 * - Close codes: 4401 unauthenticated → refresh the token, then reconnect; 4403 forbidden /
 *   wrong region → stop (a reconnect can't succeed; the next subscribe() tries again);
 *   1013 identity unavailable and anything else → reconnect with backoff.
 * - Server frames other than job events (`subscribed`, `error`, `ping`, `pong`) only feed the
 *   heartbeat timer; the server's snapshot event after `subscribed` is a normal job event.
 */

export const HEARTBEAT_MS = 25_000;

export type WsStatus = 'idle' | 'connecting' | 'open' | 'reconnecting';
export type WsListener = (event: WsServerEvent) => void;
export type WsStatusListener = (status: WsStatus, previous: WsStatus) => void;

/** The subset of the browser WebSocket the client uses (lets tests inject a fake). */
export interface WebSocketLike {
  readonly readyState: number;
  send(data: string): void;
  close(code?: number, reason?: string): void;
  onopen: ((ev: Event) => void) | null;
  onclose: ((ev: CloseEvent) => void) | null;
  onmessage: ((ev: MessageEvent) => void) | null;
  onerror: ((ev: Event) => void) | null;
}

export interface WsClientOptions {
  /** Endpoint without the token, e.g. `wss://eu.api.pitchvision.ai/api/v1/ws`. */
  url: () => string;
  getToken: () => string | null;
  refreshToken?: () => Promise<string | null>;
  createSocket?: (url: string) => WebSocketLike;
  heartbeatMs?: number;
  staleAfterMs?: number;
  backoff?: { baseMs: number; maxMs: number };
  random?: () => number;
  idleCloseMs?: number;
}

const OPEN = 1;
const AUTH_CLOSE_CODES = new Set([4401]);
const TERMINAL_CLOSE_CODES = new Set([4403]);
const SERVER_EVENT_TYPES = new Set(['progress', 'completed', 'failed']);

function isServerEvent(v: unknown): v is WsServerEvent {
  if (typeof v !== 'object' || v === null) return false;
  const o = v as Record<string, unknown>;
  return (
    typeof o.topic === 'string' &&
    typeof o.type === 'string' &&
    SERVER_EVENT_TYPES.has(o.type) &&
    typeof o.data === 'object' &&
    o.data !== null
  );
}

export class WsClient {
  private readonly opts: Required<Omit<WsClientOptions, 'refreshToken'>> &
    Pick<WsClientOptions, 'refreshToken'>;
  private readonly topics = new Map<string, Set<WsListener>>();
  private readonly statusListeners = new Set<WsStatusListener>();
  private socket: WebSocketLike | null = null;
  private status: WsStatus = 'idle';
  private attempt = 0;
  private needsRefresh = false;
  private reconnectTimer: ReturnType<typeof setTimeout> | null = null;
  private idleTimer: ReturnType<typeof setTimeout> | null = null;
  private pingTimer: ReturnType<typeof setInterval> | null = null;
  private staleTimer: ReturnType<typeof setTimeout> | null = null;
  private disposed = false;
  private opening = false;

  constructor(options: WsClientOptions) {
    const heartbeatMs = options.heartbeatMs ?? HEARTBEAT_MS;
    this.opts = {
      createSocket: (url) => new WebSocket(url),
      staleAfterMs: heartbeatMs * 2 + 5_000,
      backoff: { baseMs: 500, maxMs: 30_000 },
      random: Math.random,
      idleCloseMs: 5_000,
      ...options,
      heartbeatMs,
    };
  }

  getStatus(): WsStatus {
    return this.status;
  }

  /** Topics with at least one listener (what a reconnect re-subscribes). */
  activeTopics(): string[] {
    return [...this.topics.keys()];
  }

  subscribe(topic: string, listener: WsListener): () => void {
    let set = this.topics.get(topic);
    if (!set) {
      set = new Set();
      this.topics.set(topic, set);
      this.send({ op: 'subscribe', topic });
    }
    set.add(listener);
    this.clearIdle();
    this.ensureConnected();
    return () => {
      const current = this.topics.get(topic);
      if (!current) return;
      current.delete(listener);
      if (current.size > 0) return;
      this.topics.delete(topic);
      this.send({ op: 'unsubscribe', topic });
      if (this.topics.size === 0) this.scheduleIdleClose();
    };
  }

  onStatus(listener: WsStatusListener): () => void {
    this.statusListeners.add(listener);
    return () => this.statusListeners.delete(listener);
  }

  dispose(): void {
    this.disposed = true;
    this.topics.clear();
    this.clearIdle();
    this.clearReconnect();
    this.teardown(1000, 'dispose');
    this.setStatus('idle');
  }

  /* ---------------------------------------------------------------- internals */

  private setStatus(next: WsStatus): void {
    const prev = this.status;
    if (prev === next) return;
    this.status = next;
    for (const l of this.statusListeners) l(next, prev);
  }

  private send(op: WsClientOp): void {
    if (this.socket && this.socket.readyState === OPEN) this.socket.send(JSON.stringify(op));
  }

  private ensureConnected(): void {
    if (this.disposed || this.socket || this.reconnectTimer || this.opening) return;
    void this.connect();
  }

  private async connect(): Promise<void> {
    if (this.opening) return;
    this.opening = true;
    try {
      await this.open();
    } finally {
      this.opening = false;
    }
  }

  private canOpen(): boolean {
    return !this.disposed && !this.socket && this.topics.size > 0;
  }

  private async open(): Promise<void> {
    if (!this.canOpen()) return;
    this.setStatus(this.attempt === 0 ? 'connecting' : 'reconnecting');
    let token = this.opts.getToken();
    if ((this.needsRefresh || !token) && this.opts.refreshToken) {
      this.needsRefresh = false;
      token = await this.opts.refreshToken().catch(() => null);
    }
    if (!this.canOpen()) return;
    if (!token) {
      // Signed out: nothing to connect with. A later subscribe() tries again.
      this.setStatus('idle');
      return;
    }
    const sep = this.opts.url().includes('?') ? '&' : '?';
    const socket = this.opts.createSocket(
      `${this.opts.url()}${sep}token=${encodeURIComponent(token)}`,
    );
    this.socket = socket;
    socket.onopen = () => {
      if (this.socket !== socket) return;
      this.attempt = 0;
      this.setStatus('open');
      for (const topic of this.topics.keys()) this.send({ op: 'subscribe', topic });
      this.startHeartbeat();
    };
    socket.onmessage = (ev: MessageEvent) => {
      if (this.socket !== socket) return;
      this.armStaleTimer();
      this.dispatch(ev.data);
    };
    socket.onclose = (ev: CloseEvent) => {
      if (this.socket !== socket) return;
      this.handleDrop(ev.code);
    };
    socket.onerror = () => undefined; // a close event always follows
  }

  private dispatch(raw: unknown): void {
    if (typeof raw !== 'string') return;
    let msg: unknown;
    try {
      msg = JSON.parse(raw);
    } catch {
      return;
    }
    // Heartbeats / pongs / unknown frames only feed the stale timer.
    if (!isServerEvent(msg)) return;
    const listeners = this.topics.get(msg.topic);
    if (!listeners) return;
    for (const l of [...listeners]) l(msg);
  }

  private startHeartbeat(): void {
    this.stopHeartbeat();
    this.armStaleTimer();
    this.pingTimer = setInterval(() => this.send({ op: 'ping' }), this.opts.heartbeatMs);
  }

  private armStaleTimer(): void {
    if (this.staleTimer) clearTimeout(this.staleTimer);
    this.staleTimer = setTimeout(() => {
      // Half-open connection: no heartbeat from the server. Replace the socket.
      this.handleDrop(4000, true);
    }, this.opts.staleAfterMs);
  }

  private stopHeartbeat(): void {
    if (this.pingTimer) clearInterval(this.pingTimer);
    if (this.staleTimer) clearTimeout(this.staleTimer);
    this.pingTimer = null;
    this.staleTimer = null;
  }

  private teardown(code: number, reason: string): void {
    this.stopHeartbeat();
    const s = this.socket;
    this.socket = null;
    if (!s) return;
    s.onopen = s.onmessage = s.onerror = null;
    s.onclose = null;
    try {
      s.close(code, reason);
    } catch {
      /* already closed */
    }
  }

  private handleDrop(code: number, stale = false): void {
    this.teardown(stale ? 4000 : 1000, stale ? 'heartbeat timeout' : 'closed');
    if (AUTH_CLOSE_CODES.has(code)) this.needsRefresh = true;
    if (this.disposed || this.topics.size === 0 || TERMINAL_CLOSE_CODES.has(code)) {
      this.attempt = 0;
      this.setStatus('idle');
      return;
    }
    this.scheduleReconnect();
  }

  /** Delay before reconnect attempt `n` (0-based): base·2ⁿ capped, ±50 % jitter. */
  backoffDelay(n: number): number {
    const { baseMs, maxMs } = this.opts.backoff;
    const raw = Math.min(maxMs, baseMs * 2 ** n);
    return Math.round(raw * (0.5 + 0.5 * this.opts.random()));
  }

  private scheduleReconnect(): void {
    this.clearReconnect();
    const delay = this.backoffDelay(this.attempt);
    this.attempt += 1;
    this.setStatus('reconnecting');
    this.reconnectTimer = setTimeout(() => {
      this.reconnectTimer = null;
      void this.connect();
    }, delay);
  }

  private clearReconnect(): void {
    if (this.reconnectTimer) clearTimeout(this.reconnectTimer);
    this.reconnectTimer = null;
  }

  private scheduleIdleClose(): void {
    this.clearIdle();
    this.idleTimer = setTimeout(() => {
      this.idleTimer = null;
      if (this.topics.size > 0) return;
      this.clearReconnect();
      this.teardown(1000, 'idle');
      this.attempt = 0;
      this.setStatus('idle');
    }, this.opts.idleCloseMs);
  }

  private clearIdle(): void {
    if (this.idleTimer) clearTimeout(this.idleTimer);
    this.idleTimer = null;
  }
}

/** `VITE_API_URL` (or this origin) with ws(s):// and the WS path. */
export function wsEndpoint(base = API_BASE_URL || globalThis.location.origin): string {
  return `${base.replace(/^http/, 'ws')}/api/v1/ws`;
}

let shared: WsClient | null = null;

/** The tab's single socket. */
export function getWsClient(): WsClient {
  shared ??= new WsClient({
    url: () => wsEndpoint(),
    getToken: getAccessToken,
    refreshToken: refreshAccessToken,
  });
  return shared;
}

/** Tests: drop the shared client (closes its socket). */
export function resetWsClientForTests(): void {
  shared?.dispose();
  shared = null;
}

/** Subscribe to a topic while mounted; `topic = null` subscribes to nothing. */
export function useWsTopic(topic: string | null, onEvent: WsListener): void {
  const handler = useRef(onEvent);
  useEffect(() => {
    handler.current = onEvent;
  });
  useEffect(() => {
    if (!topic) return;
    return getWsClient().subscribe(topic, (e) => handler.current(e));
  }, [topic]);
}

export function useWsStatus(): WsStatus {
  return useSyncExternalStore(
    (cb) => getWsClient().onStatus(cb),
    () => getWsClient().getStatus(),
    () => 'idle',
  );
}
