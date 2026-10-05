"""JWKS cache for identity-svc's signing keys: cached for a TTL, refetched on an unknown `kid`.

A forced refetch (unknown kid) is rate limited so garbage tokens can't turn the API into a JWKS hammer.
"""
import asyncio
import logging
import time
from typing import Any

import httpx
import jwt

log = logging.getLogger(__name__)

MIN_REFETCH_INTERVAL_S = 30.0


class JwksUnavailable(Exception):
    """The key set could not be fetched (identity-svc down / bad response) and nothing is cached."""


class JwksClient:
    def __init__(
        self,
        url: str,
        *,
        timeout_s: float = 3.0,
        ttl_s: float = 600.0,
        transport: httpx.AsyncBaseTransport | None = None,
    ) -> None:
        self.url, self.timeout_s, self.ttl_s = url, timeout_s, ttl_s
        self._transport = transport
        self._keys: dict[str, jwt.PyJWK] = {}
        self._fetched_at = float("-inf")
        self._lock = asyncio.Lock()
        self.fetch_count = 0

    async def get_key(self, kid: str) -> jwt.PyJWK | None:
        """Signing key for `kid`, or None if identity doesn't publish it (even after a refetch)."""
        if self._age() > self.ttl_s or (kid not in self._keys and self._age() >= MIN_REFETCH_INTERVAL_S):
            await self._refresh()
        return self._keys.get(kid)

    def _age(self) -> float:
        return time.monotonic() - self._fetched_at

    async def _refresh(self) -> None:
        async with self._lock:
            if self._age() < MIN_REFETCH_INTERVAL_S:
                return  # another request refreshed while we waited
            try:
                async with httpx.AsyncClient(timeout=self.timeout_s, transport=self._transport) as client:
                    resp = await client.get(self.url)
                    resp.raise_for_status()
                    data: dict[str, Any] = resp.json()
            except (httpx.HTTPError, ValueError) as e:
                log.warning("jwks fetch failed url=%s: %r", self.url, e)
                if not self._keys:
                    raise JwksUnavailable(str(e)) from None
                return  # keep serving the cached keys; retried on the next miss / expiry
            self.fetch_count += 1
            keys: dict[str, jwt.PyJWK] = {}
            for jwk in data.get("keys", []):
                kid = jwk.get("kid")
                if not kid or jwk.get("kty") != "RSA" or jwk.get("use", "sig") != "sig":
                    continue
                try:
                    keys[kid] = jwt.PyJWK(jwk, algorithm="RS256")
                except jwt.PyJWTError as e:
                    log.warning("jwks: skipping unusable key kid=%s: %r", kid, e)
            self._keys, self._fetched_at = keys, time.monotonic()
