"""Per-track team vote (S4, AD §6.3 "phân đội theo track, không theo bbox").

Each track contributes torso crops from its first observations (at most ``votes`` crops, ≥ ``every`` frames apart
so the vote sees different poses), the crops are embedded in batches once, every crop is classified by the team
model (0 / 1 / -1) and the track's team is the plurality (ties → -1). ~10 embeddings per track instead of one per
box cuts SigLIP calls by ~90 %.
"""
from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass, field

import numpy as np

from cv_core.teams.embed import Embedder, torso_crop
from cv_core.teams.model import NO_TEAM, TeamModel, vote


@dataclass
class TrackTeamVoter:
    votes: int = 10
    every: int = 5                                  # frames between two crops of the same track
    crops: list[np.ndarray] = field(default_factory=list)
    owner: list[int] = field(default_factory=list)  # track id of each crop
    _count: dict[int, int] = field(default_factory=dict)
    _last: dict[int, int] = field(default_factory=dict)

    def offer(self, track_id: int, frame_idx: int, image: np.ndarray, box: Sequence[float] | np.ndarray) -> bool:
        """Keep a crop of ``track_id`` if it still needs votes and the last one is ≥ ``every`` frames old."""
        n = self._count.get(track_id, 0)
        if n >= self.votes or frame_idx - self._last.get(track_id, -(10**9)) < self.every:
            return False
        c = torso_crop(image, box)
        if c is None:
            return False
        self.crops.append(c)
        self.owner.append(track_id)
        self._count[track_id] = n + 1
        self._last[track_id] = frame_idx
        return True

    def resolve(self, model: TeamModel, embedder: Embedder) -> tuple[dict[int, int], dict[int, list[int]]]:
        """→ ({track_id: team}, {track_id: per-crop labels}); tracks without crops are absent (team -1)."""
        if not self.crops:
            return {}, {}
        labels = model.predict(embedder(self.crops))
        per: dict[int, list[int]] = {}
        for tid, lab in zip(self.owner, labels.tolist(), strict=True):
            per.setdefault(tid, []).append(int(lab))
        return {tid: vote(v) for tid, v in per.items()}, per


def team_column(track_ids: np.ndarray, teams: dict[int, int]) -> np.ndarray:
    """int8 team per row from the per-track result (unknown / untracked → -1)."""
    out = np.full(len(track_ids), NO_TEAM, np.int8)
    for i, tid in enumerate(np.asarray(track_ids).tolist()):
        out[i] = teams.get(int(tid), NO_TEAM)
    return out
