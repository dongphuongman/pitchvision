"""WebSocket `GET /api/v1/ws?token=<access token>` (M1-BE-12, AD §10.3): one socket per tab, topic subscriptions.

client → server  {"op": "subscribe" | "unsubscribe", "topic": "job:<uuid>"} · {"op": "ping"}
server → client  pitchvision_schemas.ws events: {"topic", "type": "progress" | "completed" | "failed", "data"}
                 + control messages:
                 {"type": "subscribed", "topic", "data": {"status"}}  then a snapshot event of the job's current state
                 {"type": "unsubscribed", "topic"} · {"type": "pong"} · {"type": "ping"} (heartbeat, every 25 s)
                 {"type": "error", "topic"?, "data": {"code", "detail"}}  codes: VALIDATION_FAILED, NOT_FOUND
                 (unknown job or another org's — indistinguishable on purpose), RATE_LIMITED
Close codes: 4401 unauthenticated · 4403 forbidden (no org / wrong region) · 1013 identity unavailable, retry.
"""

import asyncio
import contextlib
import json
import logging
import time
import uuid
from collections import deque
from typing import Annotated, Any

from fastapi import APIRouter, Query, WebSocket, WebSocketDisconnect
from pitchvision_schemas import ClientOp, CompletedEvent, ErrorCode, FailedEvent, JobStatus, ProgressEvent
from pitchvision_schemas.ws import CompletedData, FailedData, ProgressData, SegmentCounts
from pydantic import ValidationError

from pitchvision_api.auth import Principal, websocket_principal
from pitchvision_api.db import org_session
from pitchvision_api.db.models import Job
from pitchvision_api.errors import ApiError
from pitchvision_api.repositories import jobs as jobs_repo
from pitchvision_api.schemas.jobs import job_error
from pitchvision_api.services.events import QUEUE_SIZE, EventHub
from pitchvision_api.settings import Settings

log = logging.getLogger(__name__)

router = APIRouter()

MAX_MESSAGE_BYTES = 4096
CLOSE_UNAUTHENTICATED, CLOSE_FORBIDDEN, CLOSE_TRY_AGAIN = 4401, 4403, 1013


def hub_of(websocket: WebSocket) -> EventHub:
    hub: EventHub = websocket.app.state.events
    return hub


@router.websocket("/ws")
async def websocket_endpoint(websocket: WebSocket, token: Annotated[str | None, Query(max_length=8192)] = None) -> None:
    await websocket.accept()
    try:
        principal = await websocket_principal(websocket, token)
    except ApiError as e:
        code = CLOSE_UNAUTHENTICATED if e.status == 401 else CLOSE_TRY_AGAIN if e.status >= 500 else CLOSE_FORBIDDEN
        await websocket.close(code=code, reason=e.code.value)
        return
    await _Session(websocket, principal, hub_of(websocket), websocket.app.state.settings).run()


class _Session:
    def __init__(self, ws: WebSocket, principal: Principal, hub: EventHub, settings: Settings) -> None:
        self.ws, self.principal, self.hub, self.settings = ws, principal, hub, settings
        self.queue: asyncio.Queue[str] = asyncio.Queue(maxsize=QUEUE_SIZE)
        self.topics: set[str] = set()
        self.recent_subscribes: deque[float] = deque()
        self.send_lock = asyncio.Lock()  # receive/forward/heartbeat tasks all write to the socket

    async def run(self) -> None:
        tasks = [asyncio.create_task(c) for c in (self._receive(), self._forward(), self._heartbeat())]
        try:
            await asyncio.wait(tasks, return_when=asyncio.FIRST_COMPLETED)
        finally:
            for t in tasks:
                t.cancel()
            for t in tasks:
                with contextlib.suppress(BaseException):
                    await t
            for topic in list(self.topics):
                with contextlib.suppress(Exception):
                    await self.hub.unsubscribe(topic, self.queue)
            with contextlib.suppress(Exception):
                await self.ws.close()

    async def _send(self, msg: dict[str, Any] | str) -> None:
        async with self.send_lock:
            await self.ws.send_text(msg if isinstance(msg, str) else json.dumps(msg))

    async def _forward(self) -> None:
        while True:
            await self._send(await self.queue.get())

    async def _heartbeat(self) -> None:
        while True:
            await asyncio.sleep(self.settings.ws_heartbeat_s)
            await self._send({"type": "ping", "ts": int(time.time())})

    async def _receive(self) -> None:
        try:
            while True:
                raw = await self.ws.receive_text()
                await self._handle(raw)
        except WebSocketDisconnect:
            return

    async def _error(self, code: ErrorCode, detail: str, topic: str | None = None) -> None:
        msg: dict[str, Any] = {"type": "error", "data": {"code": code.value, "detail": detail}}
        if topic:
            msg["topic"] = topic
        await self._send(msg)

    async def _handle(self, raw: str) -> None:
        if len(raw) > MAX_MESSAGE_BYTES:
            await self._error(ErrorCode.VALIDATION_FAILED, "message too large")
            return
        try:
            op = ClientOp.model_validate_json(raw)
        except ValidationError as e:
            await self._error(ErrorCode.VALIDATION_FAILED, e.errors()[0]["msg"] if e.errors() else "invalid message")
            return
        if op.op == "ping":
            await self._send({"type": "pong"})
        elif op.topic is None:
            await self._error(ErrorCode.VALIDATION_FAILED, f"{op.op} needs a topic")
        elif op.op == "subscribe":
            await self._subscribe(op.topic)
        else:
            if op.topic in self.topics:
                self.topics.discard(op.topic)
                await self.hub.unsubscribe(op.topic, self.queue)
            await self._send({"type": "unsubscribed", "topic": op.topic})

    def _rate_limited(self) -> bool:
        now = time.monotonic()
        while self.recent_subscribes and now - self.recent_subscribes[0] > self.settings.ws_subscribe_window_s:
            self.recent_subscribes.popleft()
        if len(self.recent_subscribes) >= self.settings.ws_subscribe_burst:
            return True
        self.recent_subscribes.append(now)
        return False

    async def _subscribe(self, topic: str) -> None:
        if self._rate_limited():
            await self._error(ErrorCode.RATE_LIMITED, "too many subscribe requests; slow down", topic)
            return
        kind, _, ident = topic.partition(":")
        if kind != "job":  # live:session:* arrives with M2
            await self._error(ErrorCode.NOT_FOUND, "unsupported topic", topic)
            return
        try:
            job_id = uuid.UUID(ident)
        except ValueError:
            await self._error(ErrorCode.VALIDATION_FAILED, "topic must be job:<uuid>", topic)
            return
        if topic not in self.topics and len(self.topics) >= self.settings.ws_max_topics:
            await self._error(ErrorCode.RATE_LIMITED, f"at most {self.settings.ws_max_topics} topics", topic)
            return
        job = await self._job(job_id)  # RLS-scoped: another org's job is simply not found
        if job is None:
            await self._error(ErrorCode.NOT_FOUND, "job not found", topic)
            return
        if topic not in self.topics:
            await self.hub.subscribe(topic, self.queue)
            self.topics.add(topic)
        await self._send({"type": "subscribed", "topic": topic, "data": {"status": job.status}})
        if (snap := snapshot(job, topic)) is not None:
            await self._send(snap.model_dump_json())

    async def _job(self, job_id: uuid.UUID) -> Job | None:
        async with org_session(self.ws.app.state.sessionmaker, self.principal.org_id) as session:
            return await jobs_repo.get(session, self.principal.org_id, job_id)


def snapshot(job: Job, topic: str) -> ProgressEvent | CompletedEvent | FailedEvent | None:
    """The job's current state as a regular event, so a client that subscribes late starts from the truth."""
    status = JobStatus(job.status)
    if status == JobStatus.done:
        return CompletedEvent(topic=topic, data=CompletedData(video_id=str(job.video_id)))
    if status == JobStatus.failed:
        err = job_error(job.error)
        code = err.code if err else ErrorCode.INTERNAL
        return FailedEvent(topic=topic, data=FailedData(code=code, retryable=bool(err and err.retryable)))
    if status == JobStatus.cancelled:
        return None
    detail = job.detail or {}
    segs = detail.get("segments")
    data = ProgressData(
        stage=job.stage or status.value,
        pct=float(job.progress),
        eta_s=detail.get("eta_s"),
        fps=detail.get("fps"),
        segments=SegmentCounts.model_validate(segs) if segs else None,
    )
    return ProgressEvent(topic=topic, data=data)
