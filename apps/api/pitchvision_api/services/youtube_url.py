"""YouTube URL allowlist + canonicalisation (AD §14: SSRF via video URL).

Only youtube.com / www.youtube.com / m.youtube.com / youtu.be are accepted, and only an 11-char video id
is kept: workers receive the rebuilt `https://www.youtube.com/watch?v=<id>`, never the user's string, so
userinfo / port / fragment / redirect tricks can't steer the downloader anywhere else.

Keep in sync with workers/pitchvision_worker/stages/ingest.py (no shared package for it yet).
"""

import re
from dataclasses import dataclass
from urllib.parse import parse_qs, urlsplit

YOUTUBE_HOSTS = frozenset({"youtube.com", "www.youtube.com", "m.youtube.com", "youtu.be"})
_VIDEO_ID = re.compile(r"[A-Za-z0-9_-]{11}")
_PATH_ID_PREFIXES = ("shorts", "live", "embed", "v")
MAX_URL_LEN = 2048


class InvalidYoutubeUrl(ValueError):
    pass


@dataclass(frozen=True)
class YoutubeRef:
    video_id: str

    @property
    def url(self) -> str:
        return f"https://www.youtube.com/watch?v={self.video_id}"


def parse_youtube_url(raw: str) -> YoutubeRef:
    s = raw.strip()
    if not s or len(s) > MAX_URL_LEN:
        raise InvalidYoutubeUrl("URL is empty or too long.")
    if any(c.isspace() or ord(c) < 0x20 or c == "\\" for c in s):
        raise InvalidYoutubeUrl("URL contains whitespace, control characters or backslashes.")
    if "://" not in s:
        s = "https://" + s
    try:
        u = urlsplit(s)
        port = u.port
    except ValueError:
        raise InvalidYoutubeUrl("URL could not be parsed.") from None
    if u.scheme.lower() not in ("http", "https"):
        raise InvalidYoutubeUrl("Only http(s) YouTube links are accepted.")
    if u.username is not None or u.password is not None:
        raise InvalidYoutubeUrl("URLs with credentials are not accepted.")
    if port not in (None, 80, 443):
        raise InvalidYoutubeUrl("URLs with a custom port are not accepted.")
    host = (u.hostname or "").lower()
    if host not in YOUTUBE_HOSTS:
        raise InvalidYoutubeUrl("Only youtube.com and youtu.be links are supported.")

    segments = [p for p in u.path.split("/") if p]
    candidate: str | None = None
    if host == "youtu.be":
        candidate = segments[0] if len(segments) == 1 else None
    elif segments == ["watch"]:
        values = parse_qs(u.query).get("v", [])
        candidate = values[0] if len(values) == 1 else None
    elif len(segments) == 2 and segments[0] in _PATH_ID_PREFIXES:
        candidate = segments[1]
    if candidate is None or not _VIDEO_ID.fullmatch(candidate):
        raise InvalidYoutubeUrl("Link must point at a single YouTube video (watch?v=…, youtu.be/…, /shorts/…).")
    return YoutubeRef(candidate)
