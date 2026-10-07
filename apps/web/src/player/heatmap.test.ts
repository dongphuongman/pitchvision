import { heatColor, smoothGrid, spreadEllipse } from './heatmap';

describe('SCR-20c heatmap maths', () => {
  it('smoothGrid spreads a spike symmetrically and keeps its mass away from the edges', () => {
    const cols = 21;
    const rows = 15;
    const g = new Array<number>(cols * rows).fill(0);
    g[7 * cols + 10] = 100;
    const s = smoothGrid(g, cols, rows, 1.5);
    const sum = s.reduce((a, b) => a + b, 0);
    expect(sum).toBeCloseTo(100, 3);
    expect(s[7 * cols + 10]).toBeLessThan(100);
    expect(s[7 * cols + 9]).toBeCloseTo(s[7 * cols + 11] ?? 0, 5);
    expect(s[6 * cols + 10]).toBeCloseTo(s[8 * cols + 10] ?? 0, 5);
    expect(smoothGrid(g, cols, rows, 0)).toEqual(Float32Array.from(g));
  });

  it('spreadEllipse: axis-aligned and rotated covariances', () => {
    expect(spreadEllipse([9, 4, 0])).toEqual({ rx: 3, ry: 2, angle: 0 });
    const r = spreadEllipse([5, 5, 4]); // eigenvalues 9 and 1 along the diagonal
    expect(r.rx).toBeCloseTo(3);
    expect(r.ry).toBeCloseTo(1);
    expect(r.angle).toBeCloseTo(Math.PI / 4);
  });

  it('heatColor runs cold → hot and is transparent at zero', () => {
    expect(heatColor(0)[3]).toBe(0);
    expect(heatColor(1)).toEqual([235, 60, 40, 217]);
    expect(heatColor(0.45).slice(0, 3)).toEqual([250, 220, 60]);
  });
});
