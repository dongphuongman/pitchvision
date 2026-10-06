"""Linear assignment with a cost limit (the ``lap.lapjv(extend_cost=True, cost_limit=...)`` semantics ByteTrack uses).

A pair ``(i, j)`` is matched only if that lowers the total versus leaving both unmatched at ``thresh / 2`` each, i.e.
only pairs with ``cost < thresh`` can match. The problem is split into connected components of the feasible-pair
graph (players are spatially apart, so components are tiny) and each is solved exactly with the O(n³) Hungarian
algorithm on the extended (rows + cols)² matrix. Pure Python/numpy: no scipy/lap runtime dependency.
"""
from __future__ import annotations

import numpy as np

_BIG = 1e9


def hungarian(cost: list[list[float]]) -> list[int]:
    """Min-cost perfect assignment of a square matrix → ``col_of_row`` (shortest augmenting path, potentials)."""
    n = len(cost)
    u = [0.0] * (n + 1)
    v = [0.0] * (n + 1)
    p = [0] * (n + 1)
    way = [0] * (n + 1)
    for i in range(1, n + 1):
        p[0] = i
        j0 = 0
        minv = [float("inf")] * (n + 1)
        used = [False] * (n + 1)
        while True:
            used[j0] = True
            i0, delta, j1 = p[j0], float("inf"), 0
            row = cost[i0 - 1]
            for j in range(1, n + 1):
                if not used[j]:
                    cur = row[j - 1] - u[i0] - v[j]
                    if cur < minv[j]:
                        minv[j], way[j] = cur, j0
                    if minv[j] < delta:
                        delta, j1 = minv[j], j
            for j in range(n + 1):
                if used[j]:
                    u[p[j]] += delta
                    v[j] -= delta
                else:
                    minv[j] -= delta
            j0 = j1
            if p[j0] == 0:
                break
        while j0:
            j1 = way[j0]
            p[j0] = p[j1]
            j0 = j1
    col_of_row = [0] * n
    for j in range(1, n + 1):
        if p[j]:
            col_of_row[p[j] - 1] = j - 1
    return col_of_row


def _solve_extended(c: np.ndarray, thresh: float) -> list[tuple[int, int]]:
    n, m = c.shape
    size = n + m
    ext = np.full((size, size), _BIG)
    ext[:n, :m] = np.where(c < thresh, c, _BIG)
    ext[:n, m:] = np.where(np.eye(n, dtype=bool), thresh / 2, _BIG)   # row i unmatched
    ext[n:, :m] = np.where(np.eye(m, dtype=bool), thresh / 2, _BIG)   # col j unmatched
    ext[n:, m:] = 0.0
    sol = hungarian(ext.tolist())
    return [(i, sol[i]) for i in range(n) if sol[i] < m and c[i, sol[i]] < thresh]


def linear_assignment(cost: np.ndarray, thresh: float) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
    """→ ``(matches [K, 2], unmatched_rows, unmatched_cols)``; only pairs with ``cost < thresh`` are matched."""
    cost = np.asarray(cost, np.float64)
    n, m = cost.shape if cost.ndim == 2 else (0, 0)
    if n == 0 or m == 0:
        return np.empty((0, 2), np.int64), np.arange(n), np.arange(m)
    feas = cost < thresh
    # connected components of the bipartite feasible graph (rows 0..n-1, cols n..n+m-1), union-find
    parent = list(range(n + m))

    def find(a: int) -> int:
        while parent[a] != a:
            parent[a] = parent[parent[a]]
            a = parent[a]
        return a

    for i, j in zip(*np.nonzero(feas), strict=True):
        ra, rb = find(int(i)), find(n + int(j))
        if ra != rb:
            parent[ra] = rb
    groups: dict[int, tuple[list[int], list[int]]] = {}
    for i in np.flatnonzero(feas.any(1)):
        groups.setdefault(find(int(i)), ([], []))[0].append(int(i))
    for j in np.flatnonzero(feas.any(0)):
        groups.setdefault(find(n + int(j)), ([], []))[1].append(int(j))
    matches: list[tuple[int, int]] = []
    for rows, cols in groups.values():
        if len(rows) == 1 and len(cols) == 1:
            matches.append((rows[0], cols[0]))
            continue
        sub = cost[np.ix_(rows, cols)]
        matches += [(rows[a], cols[b]) for a, b in _solve_extended(sub, thresh)]
    matches.sort()
    mr = {i for i, _ in matches}
    mc = {j for _, j in matches}
    return (np.array(matches, np.int64).reshape(-1, 2), np.array([i for i in range(n) if i not in mr], np.int64),
            np.array([j for j in range(m) if j not in mc], np.int64))
