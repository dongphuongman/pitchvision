import { http, HttpResponse } from 'msw';
import type { ManualCalibration, PlayerOut } from '@/api/corrections';
import { generalPosition, type Pt } from '@/features/calibration/geometry';
import pitchTemplate from './pitchTemplate.json';
import { MOCK_TEAMS, mockShots } from './player';
import { mockJob, RUNNING_JOB, type MockJob, type MockTeam, type MockVideoDb } from './videos';

/**
 * SCR-14 / SCR-15 correction endpoints (`/api/v1/results/…`), shaped like apps/api/openapi.json:
 * teams + sample sprite sheet, candidate player boxes, manual team seeds, calibration state,
 * still frames and the FIFA pitch template. Images are inline SVG data URLs so the dev server
 * (msw/browser) shows something meaningful without object storage. Request bodies are logged as
 * `<METHOD> <what> <videoId> <json>` in `db.log` for test assertions.
 */

const VIDEO_W = 1280;
const VIDEO_H = 720;
const FRAME_MS = 40;
const CROP_W = 64;
const CROP_H = 128;
const CANDIDATE_FRAMES = 20;
const HEX = /^#[0-9a-f]{6}$/i;

function problem(status: number, code: string, title: string, ext: Record<string, unknown> = {}) {
  return HttpResponse.json(
    {
      type: `https://docs.pitchvision.ai/errors/${code.toLowerCase()}`,
      title,
      status,
      code,
      ...ext,
    },
    { status, headers: { 'Content-Type': 'application/problem+json' } },
  );
}

function svgUrl(svg: string): string {
  return `data:image/svg+xml,${encodeURIComponent(svg)}`;
}

/** Deterministic pseudo-random in [0, 1). */
function rand(seed: number): number {
  const x = Math.sin(seed * 9301 + 49297) * 233280;
  return x - Math.floor(x);
}

/** Candidate player boxes of sample frame `i` ([x1, y1, x2, y2], normalized-video pixels). */
export function mockCandidateBoxes(i: number): [number, number, number, number][] {
  return Array.from({ length: 10 }, (_, k) => {
    const x = Math.round(80 + rand(i * 31 + k) * 1080);
    const y = Math.round(260 + rand(i * 17 + k * 7) * 360);
    return [x, y, x + 28, y + 64];
  });
}

/** Cluster a mock candidate belongs to (alternating), only used to colour the dev images. */
const kitOf = (k: number) => (MOCK_TEAMS[k % 2] ?? MOCK_TEAMS[0])?.color ?? '#ffffff';

function frameSvg(tMs: number): string {
  const i = Math.floor(tMs / 3000) % CANDIDATE_FRAMES;
  const players = mockCandidateBoxes(i)
    .map(
      ([x1, y1, x2, y2], k) =>
        `<rect x="${x1 + 6}" y="${y1 + 10}" width="${x2 - x1 - 12}" height="${(y2 - y1) / 2}" rx="5" fill="${kitOf(k)}"/>` +
        `<circle cx="${(x1 + x2) / 2}" cy="${y1 + 6}" r="6" fill="#e8c6a0"/>`,
    )
    .join('');
  return (
    `<svg xmlns="http://www.w3.org/2000/svg" width="${VIDEO_W}" height="${VIDEO_H}" viewBox="0 0 ${VIDEO_W} ${VIDEO_H}">` +
    `<rect width="100%" height="100%" fill="#2f7d3b"/>` +
    `<g fill="none" stroke="#f4f4f4" stroke-width="4">` +
    `<path d="M40 180 L1240 180 L1270 700 L10 700 Z"/>` +
    `<path d="M880 180 L1240 180 L1252 420 L840 420 Z"/>` +
    `<path d="M1060 180 L1240 180 L1246 280 L1050 280 Z"/>` +
    `<path d="M640 180 L640 700"/><ellipse cx="640" cy="400" rx="150" ry="70"/></g>` +
    players +
    `<text x="24" y="40" font-family="sans-serif" font-size="26" fill="#fff">mock frame t=${tMs} ms</text>` +
    `</svg>`
  );
}

function sheetSvg(teams: MockTeam[]): string {
  const crops = teams
    .flatMap((team, row) =>
      Array.from({ length: 8 }, (_, i) => {
        const x = i * CROP_W;
        const y = row * CROP_H;
        return (
          `<rect x="${x}" y="${y}" width="${CROP_W}" height="${CROP_H}" fill="#2f7d3b"/>` +
          `<circle cx="${x + 32}" cy="${y + 26}" r="12" fill="#e8c6a0"/>` +
          `<rect x="${x + 16}" y="${y + 40}" width="32" height="46" rx="6" fill="${MOCK_TEAMS[team.cluster]?.color ?? team.color}"/>` +
          `<rect x="${x + 18}" y="${y + 86}" width="28" height="34" fill="#222"/>`
        );
      }),
    )
    .join('');
  return `<svg xmlns="http://www.w3.org/2000/svg" width="${CROP_W * 8}" height="${CROP_H * 2}">${crops}</svg>`;
}

let jobSeq = 0;

export function resultsHandlers(db: MockVideoDb) {
  const find = (id: string) => db.videos.find((v) => v.id === id && v.status !== 'deleted');
  const jobsOf = (videoId: string) =>
    db.jobs
      .filter((j) => j.video_id === videoId)
      .sort((a, b) => b.created_at.localeCompare(a.created_at));
  const doneJob = (videoId: string) => jobsOf(videoId).find((j) => j.status === 'done');
  const runningJob = (videoId: string) =>
    jobsOf(videoId).find((j) => RUNNING_JOB.includes(j.status));

  const teamsOf = (videoId: string): MockTeam[] => {
    let teams = db.teams.get(videoId);
    if (!teams) {
      teams = MOCK_TEAMS.map((t) => ({
        id: `00000000-0000-4000-a000-${videoId.slice(-10)}0${t.cluster}`,
        cluster: t.cluster,
        name: t.cluster === 0 ? 'Team A' : 'Team B',
        color: t.color,
        confirmed: false,
        attack_h1: t.cluster === 0 ? ('left' as const) : ('right' as const),
        attack_source: 'auto' as const,
      }));
      db.teams.set(videoId, teams);
    }
    return teams;
  };
  const playersOf = (videoId: string): PlayerOut[] => {
    let rows = db.players.get(videoId);
    if (!rows) {
      const p = (track_id: number, role: string, team: number | null, seconds: number) => ({
        track_id,
        role,
        team,
        confirmed: false,
        seconds,
        t0_ms: 0,
        t1_ms: seconds * 1000,
      });
      rows = [
        p(7, 'goalkeeper', 0, 1520),
        p(4, 'referee', null, 1310),
        p(12, 'goalkeeper', 0, 640),
      ];
      db.players.set(videoId, rows);
    }
    return rows;
  };
  const teamList = (videoId: string) => {
    const teams = teamsOf(videoId);
    return {
      job_id: doneJob(videoId)?.id ?? null,
      sample_sheet: {
        url: svgUrl(sheetSvg(teams)),
        expires_in: 3600,
        width: CROP_W * 8,
        height: CROP_H * 2,
      },
      items: teams.map((t) => ({
        ...t,
        samples: Array.from({ length: 8 }, (_, i) => ({
          x: i * CROP_W,
          y: t.cluster * CROP_H,
          w: CROP_W,
          h: CROP_H,
        })),
      })),
    };
  };
  /** A derived job (team_rerun / recalibrate) of the latest finished analysis. */
  const derivedJob = (videoId: string, parent: MockJob, kind: MockJob['kind']): MockJob => {
    jobSeq += 1;
    const job = mockJob(
      { id: videoId },
      {
        id: `00000000-0000-4000-b000-${String(jobSeq).padStart(12, '0')}`,
        kind,
        parent_job_id: parent.id,
        status: 'queued',
        stage: null,
        progress: 0,
        created_at: new Date(Date.now() + jobSeq).toISOString(),
        started_at: null,
      },
    );
    db.jobs.push(job);
    return job;
  };
  const busy = (job: MockJob) =>
    problem(409, 'CONFLICT', 'A job is already running', {
      detail: 'Wait for the running job to finish.',
      job_id: job.id,
    });
  const noAnalysis = () =>
    problem(409, 'CONFLICT', 'No finished analysis', {
      detail: 'The video has no finished analysis yet.',
      reason: 'no_analysis',
    });
  const invalid = (title: string, reason: string) =>
    problem(422, 'VALIDATION_FAILED', title, { detail: title, reason });

  return [
    http.get('*/api/v1/results/pitch-template', () => HttpResponse.json(pitchTemplate)),

    // ---------------------------------------------------------------- SCR-14
    http.get('*/api/v1/results/:id/teams', ({ params }) => {
      const v = find(String(params.id));
      if (!v) return problem(404, 'NOT_FOUND', 'Video not found');
      if (!doneJob(v.id)) return HttpResponse.json({ items: [], job_id: null, sample_sheet: null });
      return HttpResponse.json(teamList(v.id));
    }),
    http.patch('*/api/v1/results/:id/teams', async ({ params, request }) => {
      const v = find(String(params.id));
      if (!v) return problem(404, 'NOT_FOUND', 'Video not found');
      const body = (await request.json()) as {
        teams?: { cluster: number; name: string; color: string }[];
        swap?: boolean;
        confirmed?: boolean;
        attack_h1?: 'left' | 'right' | null;
      };
      db.log.push(`PATCH teams ${v.id} ${JSON.stringify(body)}`);
      if (!doneJob(v.id)) {
        return problem(409, 'CONFLICT', 'No teams yet', {
          detail: 'Teams exist once the analysis has finished.',
          reason: 'no_teams',
        });
      }
      const teams = teamsOf(v.id);
      for (const edit of body.teams ?? []) {
        const name = edit.name.trim();
        if (name.length < 1 || name.length > 40 || !HEX.test(edit.color)) {
          return problem(422, 'VALIDATION_FAILED', 'Invalid team');
        }
        const team = teams.find((t) => t.cluster === edit.cluster);
        if (team) Object.assign(team, { name, color: edit.color.toLowerCase() });
      }
      const [a, b] = teams;
      if (body.swap && a && b) {
        [a.name, b.name, a.color, b.color] = [b.name, a.name, b.color, a.color];
      }
      if (body.confirmed ?? true) for (const t of teams) t.confirmed = true;
      if (body.attack_h1) {
        for (const t of teams) {
          const other = body.attack_h1 === 'left' ? 'right' : 'left';
          t.attack_h1 = t.cluster === 0 ? body.attack_h1 : other;
          t.attack_source = 'user';
        }
      }
      return HttpResponse.json(teamList(v.id));
    }),
    http.get('*/api/v1/results/:id/stats', ({ params }) => {
      const v = find(String(params.id));
      if (!v) return problem(404, 'NOT_FOUND', 'Video not found');
      return HttpResponse.json(mockStats(v.id, teamsOf(v.id)));
    }),
    http.get('*/api/v1/results/:id/passes', ({ params, request }) => {
      const v = find(String(params.id));
      if (!v) return problem(404, 'NOT_FOUND', 'Video not found');
      const cluster = new URL(request.url).searchParams.get('cluster');
      return HttpResponse.json({ items: mockPasses(cluster === null ? null : Number(cluster)) });
    }),
    http.get('*/api/v1/results/:id/heatmap', ({ params, request }) => {
      const v = find(String(params.id));
      if (!v) return problem(404, 'NOT_FOUND', 'Video not found');
      const q = new URL(request.url).searchParams;
      const cluster = q.get('cluster');
      const track = q.get('track_id');
      return HttpResponse.json(
        mockHeatmap(
          cluster === null ? null : Number(cluster),
          track === null ? null : Number(track),
        ),
      );
    }),
    http.get('*/api/v1/results/:id/players', ({ params }) => {
      const v = find(String(params.id));
      if (!v) return problem(404, 'NOT_FOUND', 'Video not found');
      return HttpResponse.json({ items: playersOf(v.id) });
    }),
    http.patch('*/api/v1/results/:id/players', async ({ params, request }) => {
      const v = find(String(params.id));
      if (!v) return problem(404, 'NOT_FOUND', 'Video not found');
      const body = (await request.json()) as {
        changes: { track_id: number; role: string; team?: number | null }[];
      };
      db.log.push(`PATCH players ${v.id} ${JSON.stringify(body)}`);
      const rows = playersOf(v.id);
      for (const c of body.changes) {
        const p = rows.find((r) => r.track_id === c.track_id);
        if (!p) return invalid('Unknown track', 'unknown_track');
        Object.assign(p, {
          role: c.role,
          team: c.role === 'referee' ? null : (c.team ?? null),
          confirmed: true,
        });
      }
      return HttpResponse.json({ items: rows });
    }),
    http.get('*/api/v1/results/:id/teams/candidates', ({ params }) => {
      const v = find(String(params.id));
      const source = v
        ? jobsOf(v.id).find(
            (j) =>
              j.status === 'done' ||
              (j.status === 'failed' && j.error?.code === 'TEAM_SEPARATION_FAILED'),
          )
        : undefined;
      if (!v || !source) return problem(404, 'NOT_FOUND', 'No team samples for this analysis');
      return HttpResponse.json({
        job_id: source.id,
        fps: 25,
        frames: Array.from({ length: CANDIDATE_FRAMES }, (_, i) => ({
          t_ms: i * 3000,
          frame_idx: (i * 3000) / FRAME_MS,
          boxes: mockCandidateBoxes(i),
        })),
      });
    }),
    http.post('*/api/v1/results/:id/teams/manual', async ({ params, request }) => {
      const v = find(String(params.id));
      if (!v) return problem(404, 'NOT_FOUND', 'Video not found');
      if (!request.headers.get('Idempotency-Key')) {
        return problem(400, 'IDEMPOTENCY_KEY_REQUIRED', 'Idempotency-Key header is required');
      }
      const body = (await request.json()) as { samples: Record<string, unknown[] | undefined> };
      db.log.push(`POST teams/manual ${v.id} ${JSON.stringify(body)}`);
      const running = runningJob(v.id);
      if (running) return busy(running);
      for (const team of ['0', '1']) {
        const n = body.samples[team]?.length ?? 0;
        if (n < 2 || n > 20) return invalid(`team ${team}: pick 2–20 players (got ${n})`, 'count');
      }
      const parent =
        doneJob(v.id) ??
        jobsOf(v.id).find(
          (j) => j.status === 'failed' && j.error?.code === 'TEAM_SEPARATION_FAILED',
        );
      if (!parent) return noAnalysis();
      return HttpResponse.json(derivedJob(v.id, parent, 'team_rerun'), { status: 202 });
    }),

    // ---------------------------------------------------------------- SCR-15
    http.get('*/api/v1/results/:id/calibration/frame', ({ params, request }) => {
      const v = find(String(params.id));
      if (!v) return problem(404, 'NOT_FOUND', 'Video not found');
      if (jobsOf(v.id).length === 0) {
        return problem(404, 'NOT_FOUND', 'Video not normalized yet', { reason: 'no_video' });
      }
      const raw = Number(new URL(request.url).searchParams.get('t_ms'));
      const t = Math.max(0, Math.round((Number.isFinite(raw) ? raw : 0) / FRAME_MS) * FRAME_MS);
      if (t >= (v.duration_ms ?? 0)) {
        return problem(422, 'VALIDATION_FAILED', 'Past the end of the video', {
          reason: 'out_of_range',
        });
      }
      return HttpResponse.json({ url: svgUrl(frameSvg(t)), t_ms: t, expires_in: 3600 });
    }),
    http.get('*/api/v1/results/:id/calibration', ({ params }) => {
      const v = find(String(params.id));
      if (!v) return problem(404, 'NOT_FOUND', 'Video not found');
      const done = doneJob(v.id);
      return HttpResponse.json({
        pitch_calibration: done ? (done.pitch_calibration ?? 'ok') : null,
        job_id: done?.id ?? null,
        active_job: runningJob(v.id) ?? null,
        last_manual: db.calibrations.get(v.id) ?? null,
      });
    }),
    http.post('*/api/v1/results/:id/calibration', async ({ params, request }) => {
      const v = find(String(params.id));
      if (!v) return problem(404, 'NOT_FOUND', 'Video not found');
      if (!request.headers.get('Idempotency-Key')) {
        return problem(400, 'IDEMPOTENCY_KEY_REQUIRED', 'Idempotency-Key header is required');
      }
      const body = (await request.json()) as {
        points: { template_index: number; x: number; y: number; frame_ms: number }[];
        scope?: 'shot' | 'video';
      };
      db.log.push(`POST calibration ${v.id} ${JSON.stringify(body)}`);
      const running = runningJob(v.id);
      if (running) return busy(running);
      const parent = doneJob(v.id);
      if (!parent) return noAnalysis();
      const pts = body.points;
      if (pts.length < 4 || pts.length > 8) return invalid('Use 4–8 points', 'count');
      const frames = new Set(pts.map((p) => p.frame_ms));
      if (frames.size !== 1) return invalid('Points from several frames', 'frame_ms');
      const idx = pts.map((p) => p.template_index);
      if (new Set(idx).size !== idx.length)
        return invalid('Duplicate landmark', 'duplicate_landmark');
      const tpl = new Map(pitchTemplate.keypoints.map((k) => [k.index, [k.x, k.y] as const]));
      const image = pts.map((p): Pt => [p.x, p.y]);
      const pitch = idx.map((i): Pt => tpl.get(i) ?? [0, 0]);
      if (!generalPosition(image) || !generalPosition(pitch)) {
        return invalid('Points are collinear', 'collinear');
      }
      const frameMs = pts[0]?.frame_ms ?? 0;
      const shot = mockShots(v.duration_ms ?? 60_000).find(
        (s) => s.t0_ms <= frameMs && frameMs < s.t1_ms,
      );
      if (shot?.type !== 'main_wide') {
        return invalid('Frame is not in an analysed wide shot', 'not_analysed_shot');
      }
      const job = derivedJob(v.id, parent, 'recalibrate');
      const revision = (db.calibrations.get(v.id)?.revision ?? 1) + 1;
      const correction: ManualCalibration = {
        correction_id: `00000000-0000-4000-c000-${String(jobSeq).padStart(12, '0')}`,
        revision,
        points: pts,
        scope: body.scope ?? 'shot',
        frame_ms: frameMs,
        created_at: new Date().toISOString(),
      };
      db.calibrations.set(v.id, correction);
      return HttpResponse.json(
        { job, correction_id: correction.correction_id, revision },
        { status: 202 },
      );
    }),
  ];
}

/** Deterministic SCR-20a / SCR-20c data: team A a bit busier; 6 tracks per team with average positions. */
export function mockStats(
  _videoId: string,
  teams: readonly { cluster: number; name: string; color: string }[],
) {
  const players = teams.flatMap((t) =>
    Array.from({ length: 6 }, (_, i) => ({
      track_id: t.cluster * 100 + i + 1,
      cluster: t.cluster,
      role: i === 0 ? 'goalkeeper' : 'player',
      observed_s: 240 - i * 20,
      distance_m: 520 - i * 30 - t.cluster * 40,
      top_speed_kmh: 33.5 - i - t.cluster,
      top_speed_ms: 60_000 + i * 15_000,
      sprints: 4 - (i % 3),
      avg_x: t.cluster === 0 ? 20 + i * 12 : 85 - i * 12,
      avg_y: 14 + ((i * 11) % 40),
      cov: [36, 16, 4],
    })),
  );
  return {
    available: true,
    revision: 0,
    duration_ms: 5_400_000,
    main_wide_ms: 3_834_000,
    teams: teams.map((t) => {
      const mine = players.filter((p) => p.cluster === t.cluster);
      const dist = mine.reduce((a, p) => a + p.distance_m, 0);
      const obs = mine.reduce((a, p) => a + p.observed_s, 0);
      const top = [...mine].sort((a, b) => b.top_speed_kmh - a.top_speed_kmh)[0];
      return {
        cluster: t.cluster,
        name: t.name,
        color: t.color,
        tracks: mine.length,
        observed_player_s: obs,
        distance_m: dist,
        zones_m: { walk: dist * 0.25, jog: dist * 0.35, run: dist * 0.32, sprint: dist * 0.08 },
        sprints: mine.reduce((a, p) => a + p.sprints, 0),
        top_speed: top
          ? {
              kmh: top.top_speed_kmh,
              at_ms: top.top_speed_ms,
              track_id: top.track_id,
              cluster: t.cluster,
            }
          : null,
        m_per_min: Math.round((dist / (obs / 60)) * 10) / 10,
        estimated_distance_m: Math.round((dist / (obs / 60)) * 11 * 90),
        possession_pct: t.cluster === 0 ? 58 : 42,
        possession_ms: t.cluster === 0 ? 174_000 : 126_000,
        thirds_ms: { left: 60_000, middle: 70_000, right: 44_000 },
        passes: mockPasses(t.cluster).length,
        passes_completed: mockPasses(t.cluster).filter((p) => p.completed).length,
        balls_lost: mockPasses(t.cluster).filter((p) => !p.completed).length,
      };
    }),
    top_speeds: [],
    players,
    possession_timeline: Array.from({ length: 12 }, (_, minute) => ({
      minute,
      ms: { '0': 20_000 + ((minute * 7) % 5) * 4_000, '1': 18_000 - ((minute * 7) % 5) * 3_000 },
    })),
  };
}

export function mockHeatmap(cluster: number | null, track: number | null) {
  const cols = 42;
  const rows = 28;
  const counts = new Array<number>(cols * rows).fill(0);
  const blob = (cx: number, cy: number, n: number) => {
    for (let y = 0; y < rows; y++)
      for (let x = 0; x < cols; x++)
        counts[y * cols + x] =
          (counts[y * cols + x] ?? 0) +
          Math.round(n * Math.exp(-((x - cx) ** 2 + (y - cy) ** 2) / 18));
  };
  if (track !== null) blob(track > 100 ? 30 : 12, 10 + (track % 7) * 2, 40);
  else {
    if (cluster !== 1) {
      blob(14, 12, 30);
      blob(18, 20, 22);
    }
    if (cluster !== 0) {
      blob(28, 14, 30);
      blob(25, 8, 18);
    }
  }
  return {
    cell_m: 2.5,
    cols,
    rows,
    length_m: 105,
    width_m: 68,
    sample_s: 0.2,
    total: counts.reduce((a, b) => a + b, 0),
    counts,
  };
}

/** Deterministic SCR-20d passes: 8 for team A (6 completed), 5 for team B (3 completed). */
export function mockPasses(cluster: number | null) {
  const make = (cl: number, n: number, lostEvery: number) =>
    Array.from({ length: n }, (_, i) => ({
      t_ms: 30_000 + i * 41_000 + cl * 9_000,
      end_ms: 31_400 + i * 41_000 + cl * 9_000,
      cluster: cl,
      from_track: cl * 100 + 1 + (i % 5),
      to_track: cl * 100 + 2 + ((i + 2) % 5),
      completed: (i + 1) % lostEvery !== 0,
      x0: cl === 0 ? 20 + i * 6 : 85 - i * 8,
      y0: 15 + ((i * 13) % 38),
      x1: cl === 0 ? 34 + i * 6 : 70 - i * 8,
      y1: 22 + ((i * 17) % 30),
    }));
  const all = [...make(0, 8, 4), ...make(1, 5, 2)];
  return all
    .filter((p) => cluster === null || p.cluster === cluster)
    .sort((a, b) => a.t_ms - b.t_ms);
}
