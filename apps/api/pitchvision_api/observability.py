"""Errors + metrics (AD §17, M1-OPS-08).

- Sentry: on when ``PV_SENTRY_DSN`` is set; release = image tag, tags service / region. No PII (no request bodies,
  no user emails — only the user id Sentry derives from nothing: ``send_default_pii=False``).
- ``GET /metrics`` (Prometheus text, outside /api/v1): HTTP requests by route template + status and their latency,
  plus values read at scrape time — Celery queue depths (Redis ``LLEN``), YouTube ingest outcomes (the workers'
  Redis hash ``pv:metrics:ingest_total``) and jobs by status (``pv_job_stats``). Bearer ``PV_METRICS_TOKEN``; without
  a token the route only exists in ``PV_ENV=local``.
"""

from __future__ import annotations

import hmac
import logging
import time
from collections.abc import Iterable
from typing import Any

from fastapi import APIRouter, Request, Response
from prometheus_client import CONTENT_TYPE_LATEST, CollectorRegistry, Counter, Histogram, generate_latest
from prometheus_client.core import CounterMetricFamily, GaugeMetricFamily, Metric
from prometheus_client.registry import Collector
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncEngine
from starlette.types import ASGIApp, Message, Receive, Scope, Send

from pitchvision_api.settings import Settings

log = logging.getLogger(__name__)

QUEUES = ("orchestrate", "cpu", "gpu", "ingest")
INGEST_KEY = "pv:metrics:ingest_total"  # pitchvision_worker.metrics.REDIS_KEY
JOB_WINDOW_S = 3600


def init_sentry(settings: Settings, service: str = "api") -> bool:
    if not settings.sentry_dsn:
        return False
    import sentry_sdk

    sentry_sdk.init(dsn=settings.sentry_dsn, environment=settings.env, release=settings.image_tag,
                    traces_sample_rate=settings.sentry_traces_sample_rate, send_default_pii=False)  # fmt: skip
    sentry_sdk.set_tag("service", service)
    sentry_sdk.set_tag("region", settings.region)
    return True


class _Scraped(Collector):
    """Families gathered right before the registry renders (async sources can't be read in collect())."""

    def __init__(self) -> None:
        self.families: list[Metric] = []

    def collect(self) -> Iterable[Metric]:
        return list(self.families)


class Metrics:
    def __init__(self) -> None:
        self.registry = CollectorRegistry(auto_describe=True)
        self.requests = Counter("pv_http_requests_total", "HTTP requests", ["method", "route", "status"],
                                registry=self.registry)  # fmt: skip
        self.latency = Histogram("pv_http_request_duration_seconds", "HTTP request latency", ["method", "route"],
                                 buckets=(0.01, 0.025, 0.05, 0.1, 0.25, 0.5, 1, 2.5, 5, 10),
                                 registry=self.registry)  # fmt: skip
        self.limited = Counter("pv_rate_limited_total", "Requests refused with 429 by a per-user rate limit",
                               ["rule"], registry=self.registry)  # fmt: skip
        self.scraped = _Scraped()
        self.registry.register(self.scraped)

    def rate_limited(self, rule: str) -> None:
        self.limited.labels(rule).inc()

    async def refresh(self, redis: Any, engine: AsyncEngine) -> None:
        fams: list[Metric] = []
        up = GaugeMetricFamily("pv_dependency_up", "Dependency reachable during the last scrape", labels=["dependency"])
        try:
            depth = GaugeMetricFamily("pv_queue_depth", "Celery tasks waiting per queue", labels=["queue"])
            for q in QUEUES:
                depth.add_metric([q], float(await redis.llen(q)))
            ingest = CounterMetricFamily("pv_ingest", "YouTube ingest outcomes (workers, M1-OPS-06)",
                                         labels=["op", "outcome", "reason"])  # fmt: skip
            for field, n in (await redis.hgetall(INGEST_KEY)).items():
                parts = (field.decode() if isinstance(field, bytes) else str(field)).split(":", 2)
                if len(parts) == 3:
                    ingest.add_metric(parts, float(n))
            fams += [depth, ingest]
            up.add_metric(["redis"], 1)
        except Exception as e:
            log.warning("metrics: redis unavailable: %r", e)
            up.add_metric(["redis"], 0)
        try:
            async with engine.connect() as conn:
                rows = (await conn.execute(text("SELECT status, total, recent FROM pv_job_stats(:w)"),
                                           {"w": JOB_WINDOW_S})).all()  # fmt: skip
            jobs = GaugeMetricFamily("pv_jobs", "Jobs by status", labels=["status"])
            recent = GaugeMetricFamily("pv_jobs_finished_last_hour", "Jobs that reached a status in the last hour",
                                       labels=["status"])  # fmt: skip
            for status, total, last_hour in rows:
                jobs.add_metric([status], float(total))
                recent.add_metric([status], float(last_hour))
            fams += [jobs, recent]
            up.add_metric(["postgres"], 1)
        except Exception as e:
            log.warning("metrics: database unavailable: %r", e)
            up.add_metric(["postgres"], 0)
        self.scraped.families = [*fams, up]

    def render(self) -> bytes:
        return generate_latest(self.registry)


class MetricsMiddleware:
    """Pure ASGI: counts every HTTP request by its route template (``/api/v1/videos/{video_id}``), not the raw path."""

    def __init__(self, app: ASGIApp, metrics: Metrics) -> None:
        self.app, self.metrics = app, metrics

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return
        start, status = time.perf_counter(), 500

        async def send_status(message: Message) -> None:
            nonlocal status
            if message["type"] == "http.response.start":
                status = message["status"]
            await send(message)

        try:
            await self.app(scope, receive, send_status)
        finally:
            route = _template(scope)
            if route not in ("/metrics", "/healthz", "/readyz"):
                method = scope.get("method", "GET")
                self.metrics.requests.labels(method, route, str(status)).inc()
                self.metrics.latency.labels(method, route).observe(time.perf_counter() - start)


def _template(scope: Scope) -> str:
    """``/api/v1/videos/{video_id}`` from the matched request (sub-router routes don't carry the prefix)."""
    if scope.get("route") is None:
        return "unmatched"
    path = str(scope.get("path", ""))
    for name, value in (scope.get("path_params") or {}).items():
        path = path.replace(f"/{value}", f"/{{{name}}}", 1)
    return path


router = APIRouter(include_in_schema=False)


def _allowed(request: Request, settings: Settings) -> bool:
    token = settings.metrics_token
    if not token:
        return settings.env == "local"
    got = request.headers.get("authorization", "").removeprefix("Bearer ").strip()
    return hmac.compare_digest(got.encode(), token.encode())


@router.get("/metrics")
async def metrics(request: Request) -> Response:
    settings: Settings = request.app.state.settings
    if not _allowed(request, settings):
        return Response(status_code=404)
    m: Metrics = request.app.state.metrics
    await m.refresh(request.app.state.redis, request.app.state.engine)
    return Response(m.render(), media_type=CONTENT_TYPE_LATEST)


