"""Plain-text tables for eval reports."""
from __future__ import annotations

import math
from collections.abc import Mapping, Sequence

from pveval.metrics import GroupTable


def _fmt(v: object) -> str:
    if isinstance(v, float):
        return "  -  " if math.isnan(v) else f"{v:.1%}" if v <= 1.0 else f"{v:.2f}"
    return str(v)


def table(title: str, rows: Mapping[str, Mapping[str, object]], cols: Sequence[str]) -> str:
    w0 = max([len(title), *(len(k) for k in rows)]) + 2
    head = title.ljust(w0) + "".join(c.rjust(max(10, len(c) + 2)) for c in cols)
    lines = [head, "-" * len(head)]
    for k, r in rows.items():
        lines.append(k.ljust(w0) + "".join(_fmt(r.get(c, "")).rjust(max(10, len(c) + 2)) for c in cols))
    return "\n".join(lines)


def group_tables(tables: Sequence[GroupTable], cols: Sequence[str]) -> str:
    return "\n\n".join(table(f"by {t.key}", t.rows, cols) for t in tables)
