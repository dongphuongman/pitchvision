import { fitHomography, generalPosition, isCollinear, project, type Pt } from './geometry';

describe('calibration geometry', () => {
  it('detects (nearly) collinear triples', () => {
    expect(isCollinear([0, 0], [10, 0], [20, 0.3])).toBe(true);
    expect(isCollinear([0, 0], [10, 0], [5, 8])).toBe(false);
    expect(isCollinear([3, 3], [3, 3], [9, 1])).toBe(true);
  });

  it('needs 4 points with no 3 on a line', () => {
    const line: Pt[] = [
      [0, 0],
      [10, 0],
      [20, 0],
      [30, 0.1],
    ];
    expect(generalPosition(line)).toBe(false);
    expect(generalPosition([...line, [5, 20]])).toBe(false);
    expect(
      generalPosition([
        [0, 0],
        [10, 0],
        [10, 10],
        [0, 10],
      ]),
    ).toBe(true);
    // Any good quad among more points is enough.
    expect(generalPosition([...line, [5, 20], [25, 18]])).toBe(true);
  });

  it('fits a homography that maps the points (least squares with more than 4)', () => {
    const pitch: Pt[] = [
      [88.5, 13.84],
      [105, 13.84],
      [105, 54.16],
      [88.5, 54.16],
      [94, 34],
    ];
    const truth = [9, 2, 50, 0.5, 6, 80, 0.001, 0.004, 1];
    const img = pitch.map((p) => project(truth, p) as Pt);
    const h = fitHomography(pitch, img);
    expect(h).not.toBeNull();
    if (!h) return;
    pitch.forEach((p, i) => {
      const q = project(h, p) as Pt;
      expect(q[0]).toBeCloseTo((img[i] as Pt)[0], 3);
      expect(q[1]).toBeCloseTo((img[i] as Pt)[1], 3);
    });
  });

  it('returns null for degenerate input', () => {
    expect(fitHomography([[0, 0]], [[0, 0]])).toBeNull();
    const same: Pt[] = [
      [1, 1],
      [1, 1],
      [1, 1],
      [1, 1],
    ];
    expect(fitHomography(same, same)).toBeNull();
  });
});
