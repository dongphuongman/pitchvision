import { createMockDb, mockOrg, mockUser, type MockDb } from './identity';
import { createVideoDb, mockJob, mockVideo, stubManifest, type MockVideoDb } from './videos';

/**
 * Demo data for `VITE_MOCK_API=1 pnpm dev` (password for every account: "correct horse battery"):
 *   owner@pitchvision.test       verified, owner of Riverside FC (EU) + viewer of City Academy (US)
 *   viewer@pitchvision.test      verified, viewer of Riverside FC
 *   unverified@pitchvision.test  unverified (verify link: /verify-email?token=demo-verify)
 *   noorg@pitchvision.test       verified, no organization → onboarding
 * Links: /reset-password?token=demo-reset (valid) · ?token=old (expired)
 *        /invite/demo-invite (for new@pitchvision.test) · /invite/demo-expired
 */
export function demoDb(): MockDb {
  const owner = mockUser({ email: 'owner@pitchvision.test', name: 'Dong Man' });
  const viewer = mockUser({ email: 'viewer@pitchvision.test', name: 'Val Viewer' });
  const unverified = mockUser({
    email: 'unverified@pitchvision.test',
    name: 'Una Verified',
    email_verified: false,
  });
  const noorg = mockUser({ email: 'noorg@pitchvision.test', name: 'Nora Org' });
  const riverside = mockOrg({ id: 'org-riverside', name: 'Riverside FC', region: 'eu' });
  const academy = mockOrg({
    id: 'org-academy',
    name: 'City Academy',
    kind: 'academy',
    region: 'us',
  });
  return createMockDb({
    users: [owner, viewer, unverified, noorg],
    orgs: [riverside, academy],
    memberships: [
      { user_id: owner.id, org_id: riverside.id, role: 'owner' },
      { user_id: owner.id, org_id: academy.id, role: 'viewer' },
      { user_id: viewer.id, org_id: riverside.id, role: 'viewer' },
    ],
    invites: [
      {
        token: 'demo-invite',
        org_id: riverside.id,
        email: 'new@pitchvision.test',
        role: 'editor',
        inviter_name: 'Sam Reed',
        expired: false,
      },
      {
        token: 'demo-expired',
        org_id: riverside.id,
        email: 'late@pitchvision.test',
        role: 'viewer',
        inviter_name: 'Sam Reed',
        expired: true,
      },
    ],
    resetTokens: { 'demo-reset': 'valid', old: 'expired' },
    verifyTokens: { 'demo-verify': unverified.id },
  });
}

const TEAMS = [
  ['Riverside FC', 'Harbor City'],
  ['France', 'Switzerland'],
  ['U17 Riverside', 'Ajax U17'],
  ['Northfield', 'Riverside FC'],
  ['Riverside FC', 'Eastgate Utd'],
  ['Westbrook', 'Riverside FC'],
  ['Riverside Women', 'Lakeside Women'],
] as const;

/**
 * Library demo (SCR-10): 26 videos covering every card state, so the first page (20) scrolls
 * into a second one. Jobs don't change the video status (sources stay `ready`); the latest job
 * drives the card. Running jobs advance over the mock WebSocket.
 */
export function demoVideoDb(): MockVideoDb {
  const db = createVideoDb();
  const day = (n: number, h = 18) => new Date(Date.UTC(2026, 9, 5 - n, h, 0, 0)).toISOString();
  type Kind =
    'running' | 'queued' | 'ingesting' | 'uploading' | 'failed' | 'fresh' | 'ingestFailed' | 'done';
  const kinds: Kind[] = [
    'running',
    'queued',
    'ingesting',
    'uploading',
    'failed',
    'fresh',
    'ingestFailed',
  ];
  for (let i = 0; i < 26; i += 1) {
    const [home, away] = TEAMS[i % TEAMS.length] ?? ['Home', 'Away'];
    const kind = kinds[i] ?? 'done';
    const v = mockVideo({
      title: kind === 'uploading' ? 'training_match_cam2.mp4' : `${home} vs ${away}`,
      created_at: day(i, 20 - (i % 5)),
      duration_ms:
        kind === 'uploading' || kind === 'ingesting' ? null : 5_400_000 + ((i * 137_000) % 900_000),
      source_type: kind === 'uploading' || i % 3 === 2 ? 'upload' : 'youtube',
      status:
        kind === 'uploading'
          ? 'uploading'
          : kind === 'ingesting'
            ? 'ingesting'
            : kind === 'ingestFailed'
              ? 'failed'
              : 'ready',
      error: kind === 'ingestFailed' ? { code: 'INGEST_FAILED', reason: null, detail: null } : null,
    });
    db.videos.push(v);
    if (kind === 'running') {
      db.jobs.push(mockJob(v, { status: 'inferring', progress: 42, eta_s: 540 }));
    } else if (kind === 'queued') {
      db.jobs.push(mockJob(v, { status: 'queued', stage: null, progress: 0 }));
    } else if (kind === 'failed') {
      db.jobs.push(
        mockJob(v, {
          status: 'failed',
          progress: 37,
          error: { code: 'NO_WIDE_SHOTS', retryable: true, detail: null },
        }),
      );
    } else if (kind === 'done') {
      db.jobs.push(
        mockJob(v, {
          status: 'done',
          progress: 100,
          stage: 'load_db',
          coverage: { main_wide_pct: 58 + ((i * 7) % 30) },
        }),
      );
      db.manifests[v.id] = stubManifest(v, {
        pitch: { status: i === 9 ? 'degraded' : 'ok' },
      });
    }
  }
  return db;
}
