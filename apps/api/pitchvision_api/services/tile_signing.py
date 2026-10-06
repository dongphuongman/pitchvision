"""Signed playback-tile URLs (AD §8.3, §9.5 "Phục vụ tile", §14; M1-BE-16).

The bucket is never public. The manifest hands the player ONE URL template for all tiles of a (video, job,
revision); the signature covers that *scope* and an expiry, not the tile number, so the player can fill in
``{tile_no}`` itself:

    {base}/{video_id}/{job_id}/r{revision}/{tile_no}.json.gz?exp={unix_s}&sig={b64url(HMAC-SHA256(key, msg))}
    msg = "tiles/{video_id}/{job_id}/r{revision}/\\n{exp}"

The same scheme (key = ``PV_TILE_SIGNING_KEY``) is what the Cloudflare Worker at ``tiles.{region}…`` will verify
later; self-host serves tiles from this API (``routers/tiles.py``). A tampered path, revision, job or expiry fails
the check; an expired URL is refused (403) and the player re-fetches the manifest.
"""

import base64
import hashlib
import hmac
import uuid

TILES_ROUTE = "/api/v1/tiles"


def tile_scope(video_id: uuid.UUID | str, job_id: uuid.UUID | str, revision: int) -> str:
    return f"tiles/{video_id}/{job_id}/r{revision}/"


def sign(key: bytes, scope: str, exp: int) -> str:
    mac = hmac.new(key, f"{scope}\n{exp}".encode(), hashlib.sha256).digest()
    return base64.urlsafe_b64encode(mac).rstrip(b"=").decode()


def verify(key: bytes, scope: str, exp: int, sig: str, now_s: float) -> bool:
    """Constant-time check of ``sig`` for ``scope`` + ``exp``, and ``exp`` not in the past."""
    if exp < now_s:
        return False
    return hmac.compare_digest(sign(key, scope, exp), sig)


def signed_template(key: bytes, base_url: str, video_id: uuid.UUID | str, job_id: uuid.UUID | str, revision: int,
                    now_s: float, ttl_s: int) -> str:
    exp = int(now_s) + ttl_s
    sig = sign(key, tile_scope(video_id, job_id, revision), exp)
    return f"{base_url.rstrip('/')}/{video_id}/{job_id}/r{revision}/{{tile_no}}.json.gz?exp={exp}&sig={sig}"
