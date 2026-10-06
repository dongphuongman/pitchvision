/**
 * Client-side checks mirroring the API's POST /calibration validation (apps/api
 * services/corrections.py), plus a least-squares homography for the "preview pitch lines" overlay.
 */
export type Pt = readonly [number, number];

/** |cross| / longest side² below this = three points on one line (same as the API). */
export const COLLINEAR_TOL = 0.01;

/** Element `i` of a numeric array (0 when out of range — callers stay in range). */
function at(a: ArrayLike<number>, i: number): number {
  return a[i] ?? 0;
}

export function isCollinear(a: Pt, b: Pt, c: Pt): boolean {
  const cross = Math.abs((b[0] - a[0]) * (c[1] - a[1]) - (b[1] - a[1]) * (c[0] - a[0]));
  const d2 = (p: Pt, q: Pt) => (p[0] - q[0]) ** 2 + (p[1] - q[1]) ** 2;
  const scale = Math.max(d2(a, b), d2(a, c), d2(b, c));
  return scale === 0 || cross / scale < COLLINEAR_TOL;
}

function combinations<T>(items: readonly T[], k: number): T[][] {
  if (k === 0) return [[]];
  const out: T[][] = [];
  items.forEach((item, i) => {
    for (const rest of combinations(items.slice(i + 1), k - 1)) out.push([item, ...rest]);
  });
  return out;
}

/** True when some 4 of the points have no 3 on one line (what a homography needs). */
export function generalPosition(pts: readonly Pt[]): boolean {
  return combinations(pts, 4).some((quad) =>
    combinations(quad, 3).every(([a, b, c]) => !(a && b && c && isCollinear(a, b, c))),
  );
}

/** Row-major 3×3 matrix (9 numbers). */
export type Homography = readonly number[];

/** Solves A·x = b (n×n, A row-major) by Gauss–Jordan with partial pivoting; null when singular. */
function solve(a: Float64Array, b: Float64Array): Float64Array | null {
  const n = b.length;
  const w = n + 1;
  const m = new Float64Array(n * w);
  for (let r = 0; r < n; r++) {
    for (let c = 0; c < n; c++) m[r * w + c] = at(a, r * n + c);
    m[r * w + n] = at(b, r);
  }
  for (let c = 0; c < n; c++) {
    let piv = c;
    for (let r = c + 1; r < n; r++) {
      if (Math.abs(at(m, r * w + c)) > Math.abs(at(m, piv * w + c))) piv = r;
    }
    if (Math.abs(at(m, piv * w + c)) < 1e-12) return null;
    if (piv !== c) {
      for (let k = 0; k < w; k++) {
        const tmp = at(m, c * w + k);
        m[c * w + k] = at(m, piv * w + k);
        m[piv * w + k] = tmp;
      }
    }
    const p = at(m, c * w + c);
    for (let r = 0; r < n; r++) {
      if (r === c) continue;
      const f = at(m, r * w + c) / p;
      if (f === 0) continue;
      for (let k = c; k < w; k++) m[r * w + k] = at(m, r * w + k) - f * at(m, c * w + k);
    }
  }
  const x = new Float64Array(n);
  for (let r = 0; r < n; r++) x[r] = at(m, r * w + n) / at(m, r * w + r);
  return x;
}

function mul3(a: ArrayLike<number>, b: ArrayLike<number>): number[] {
  return Array.from({ length: 9 }, (_, i) => {
    const r = Math.floor(i / 3);
    const c = i % 3;
    return (
      at(a, r * 3) * at(b, c) + at(a, r * 3 + 1) * at(b, 3 + c) + at(a, r * 3 + 2) * at(b, 6 + c)
    );
  });
}

function normalise(pts: readonly Pt[]) {
  const cx = pts.reduce((s, p) => s + p[0], 0) / pts.length;
  const cy = pts.reduce((s, p) => s + p[1], 0) / pts.length;
  const d = pts.reduce((s, p) => s + Math.hypot(p[0] - cx, p[1] - cy), 0) / pts.length || 1;
  const k = Math.SQRT2 / d;
  return { k, cx, cy, pts: pts.map((p): Pt => [(p[0] - cx) * k, (p[1] - cy) * k]) };
}

/**
 * Homography mapping `src` → `dst` (≥ 4 pairs; least squares with h33 = 1 when more). Inputs are
 * normalised (centroid + scale) for conditioning. Null when degenerate.
 */
export function fitHomography(src: readonly Pt[], dst: readonly Pt[]): Homography | null {
  if (src.length < 4 || src.length !== dst.length) return null;
  const s = normalise(src);
  const t = normalise(dst);
  const ata = new Float64Array(64);
  const atb = new Float64Array(8);
  const add = (row: readonly number[], rhs: number) => {
    for (let i = 0; i < 8; i++) {
      atb[i] = at(atb, i) + at(row, i) * rhs;
      for (let j = 0; j < 8; j++) ata[i * 8 + j] = at(ata, i * 8 + j) + at(row, i) * at(row, j);
    }
  };
  s.pts.forEach(([x, y], i) => {
    const [u, v] = t.pts[i] ?? [0, 0];
    add([x, y, 1, 0, 0, 0, -u * x, -u * y], u);
    add([0, 0, 0, x, y, 1, -v * x, -v * y], v);
  });
  const h = solve(ata, atb);
  if (!h) return null;
  // H = T⁻¹ · Hn · S  (S, T: the normalising similarities).
  const hn = [...h, 1];
  const S = [s.k, 0, -s.k * s.cx, 0, s.k, -s.k * s.cy, 0, 0, 1];
  const Ti = [1 / t.k, 0, t.cx, 0, 1 / t.k, t.cy, 0, 0, 1];
  const out = mul3(mul3(Ti, hn), S);
  const w = at(out, 8);
  if (!Number.isFinite(w) || Math.abs(w) < 1e-12) return null;
  return out.map((v) => v / w);
}

/** Projects a point; null behind the camera (w ≤ 0). */
export function project(h: Homography, p: Pt): Pt | null {
  const w = at(h, 6) * p[0] + at(h, 7) * p[1] + at(h, 8);
  if (w <= 1e-9) return null;
  return [
    (at(h, 0) * p[0] + at(h, 1) * p[1] + at(h, 2)) / w,
    (at(h, 3) * p[0] + at(h, 4) * p[1] + at(h, 5)) / w,
  ];
}
