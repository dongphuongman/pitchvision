"""Redis pub/sub → WebSocket fan-out (AD §10.3/§10.4): ONE Redis subscription per API process, multiplexed.

Workers publish `pitchvision_schemas.ws` events on channel `job:{id}`. The hub SUBSCRIBEs a channel when its first
local listener arrives and UNSUBSCRIBEs when the last one leaves; each listener is a bounded asyncio.Queue (a slow
client loses its oldest events, never blocks the reader — the job snapshot on subscribe + GET /jobs/{id} recover).
On a Redis error the reader reconnects and re-subscribes every live channel.
"""

import asyncio
import contextlib
import logging
from typing import Any

log = logging.getLogger(__name__)

QUEUE_SIZE = 256
RECONNECT_S = 1.0


class EventHub:
    def __init__(self, redis_url: str) -> None:
        self.redis_url = redis_url
        self._subs: dict[str, set[asyncio.Queue[str]]] = {}
        self._redis: Any = None
        self._pubsub: Any = None
        self._reader: asyncio.Task[None] | None = None
        self._loop: asyncio.AbstractEventLoop | None = None
        self._lock: asyncio.Lock | None = None

    # ------------------------------------------------------------------ public
    async def subscribe(self, topic: str, queue: "asyncio.Queue[str]") -> None:
        async with self._guard():
            await self._ensure_started()
            listeners = self._subs.setdefault(topic, set())
            if not listeners:
                await self._pubsub.subscribe(topic)
            listeners.add(queue)

    async def unsubscribe(self, topic: str, queue: "asyncio.Queue[str]") -> None:
        async with self._guard():
            listeners = self._subs.get(topic)
            if listeners is None:
                return
            listeners.discard(queue)
            if not listeners:
                del self._subs[topic]
                if self._pubsub is not None:
                    with contextlib.suppress(Exception):
                        await self._pubsub.unsubscribe(topic)

    def topics(self) -> set[str]:
        return set(self._subs)

    async def close(self) -> None:
        if self._reader is not None:
            self._reader.cancel()
            with contextlib.suppress(BaseException):
                await self._reader
        self._reader = None
        await self._disconnect()
        self._subs.clear()

    # ------------------------------------------------------------------ internals
    def _guard(self) -> asyncio.Lock:
        loop = asyncio.get_running_loop()
        if self._loop is not loop:  # first use, or a new event loop (tests): start over on this loop
            self._loop, self._lock = loop, asyncio.Lock()
            self._reader, self._redis, self._pubsub = None, None, None
            self._subs.clear()
        assert self._lock is not None
        return self._lock

    async def _ensure_started(self) -> None:
        if self._pubsub is None:
            await self._connect()
        if self._reader is None or self._reader.done():
            self._reader = asyncio.create_task(self._read_loop(), name="ws-event-hub")

    async def _connect(self) -> None:
        import redis.asyncio as aioredis

        self._redis = aioredis.Redis.from_url(self.redis_url, socket_connect_timeout=3, health_check_interval=30)
        self._pubsub = self._redis.pubsub(ignore_subscribe_messages=True)
        for topic in self._subs:
            await self._pubsub.subscribe(topic)

    async def _disconnect(self) -> None:
        pubsub, client, self._pubsub, self._redis = self._pubsub, self._redis, None, None
        with contextlib.suppress(Exception):
            if pubsub is not None:
                await pubsub.aclose()
        with contextlib.suppress(Exception):
            if client is not None:
                await client.aclose()

    async def _read_loop(self) -> None:
        while True:
            try:
                if not self._subs or self._pubsub is None or not self._pubsub.subscribed:
                    await asyncio.sleep(0.2)
                    continue
                msg = await self._pubsub.get_message(ignore_subscribe_messages=True, timeout=1.0)
                if msg and msg.get("type") == "message":
                    self._dispatch(_text(msg["channel"]), _text(msg["data"]))
            except asyncio.CancelledError:
                raise
            except Exception as e:
                log.warning("event hub: redis error %r; reconnecting in %.0fs", e, RECONNECT_S)
                await self._disconnect()
                await asyncio.sleep(RECONNECT_S)
                with contextlib.suppress(Exception):
                    await self._connect()

    def _dispatch(self, topic: str, data: str) -> None:
        for queue in list(self._subs.get(topic, ())):
            if queue.full():  # slow consumer: drop its oldest event
                with contextlib.suppress(asyncio.QueueEmpty):
                    queue.get_nowait()
            queue.put_nowait(data)


def _text(v: Any) -> str:
    return v.decode() if isinstance(v, bytes | bytearray) else str(v)
