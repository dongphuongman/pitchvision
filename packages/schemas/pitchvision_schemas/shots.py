"""Shot timeline (stage S2b). Only main_wide shots are analysed; the UI greys out the rest."""
from enum import StrEnum

from pydantic import BaseModel, ConfigDict, Field, model_validator


class ShotType(StrEnum):
    main_wide = "main_wide"
    close_up = "close_up"
    replay = "replay"
    other = "other"  # crowd, graphics, transition logos


class Shot(BaseModel):
    model_config = ConfigDict(frozen=True)

    t0_ms: int = Field(..., ge=0)
    t1_ms: int = Field(..., gt=0)
    type: ShotType
    conf: float = Field(..., ge=0.0, le=1.0)

    @model_validator(mode="after")
    def _ordered(self) -> "Shot":
        if self.t1_ms <= self.t0_ms:
            raise ValueError("t1_ms must be > t0_ms")
        return self


class ShotTimeline(BaseModel):
    shots: list[Shot]

    @model_validator(mode="after")
    def _contiguous(self) -> "ShotTimeline":
        for a, b in zip(self.shots, self.shots[1:], strict=False):
            if b.t0_ms != a.t1_ms:
                raise ValueError(f"shots must be contiguous and sorted: {a.t1_ms} → {b.t0_ms}")
        return self

    def coverage(self, shot_type: ShotType = ShotType.main_wide) -> float:
        """Share of the video's duration covered by shot_type, 0..1 (stats are shown with this %)."""
        total = sum(s.t1_ms - s.t0_ms for s in self.shots)
        part = sum(s.t1_ms - s.t0_ms for s in self.shots if s.type == shot_type)
        return part / total if total else 0.0

    def at(self, ts_ms: int) -> Shot | None:
        lo, hi = 0, len(self.shots)
        while lo < hi:
            mid = (lo + hi) // 2
            if self.shots[mid].t1_ms <= ts_ms:
                lo = mid + 1
            else:
                hi = mid
        return self.shots[lo] if lo < len(self.shots) and self.shots[lo].t0_ms <= ts_ms else None
