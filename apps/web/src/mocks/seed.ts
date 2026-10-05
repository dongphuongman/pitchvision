import { createMockDb, mockOrg, mockUser, type MockDb } from './identity';

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
