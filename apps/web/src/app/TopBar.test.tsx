import { screen, waitFor, within } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import type { Role } from '@/api/identity';
import { useSignOutGuard } from '@/auth/useAuthActions';
import { mockOrg, mockUser } from '@/mocks/identity';
import { setupIdentityMock } from '@/test/msw';
import { renderApp, testSession } from '@/test/renderApp';

const mock = setupIdentityMock();

async function openAccountMenu() {
  const user = userEvent.setup();
  await user.click(await screen.findByRole('button', { name: 'Account menu' }));
  return { user, menu: await screen.findByRole('menu') };
}

describe('top bar G — role-based visibility', () => {
  it.each<[Role, { create: boolean; org: boolean; billing: boolean }]>([
    ['viewer', { create: false, org: false, billing: false }],
    ['editor', { create: true, org: false, billing: false }],
    ['admin', { create: true, org: true, billing: false }],
    ['owner', { create: true, org: true, billing: true }],
  ])('%s', async (role, expected) => {
    renderApp('/library', { auth: testSession({ role }) });
    await screen.findByRole('heading', { name: 'Library' });
    expect(!!screen.queryByRole('link', { name: 'New analysis' })).toBe(expected.create);
    // Phase C: no Live entry.
    expect(screen.queryByRole('link', { name: /live/i })).not.toBeInTheDocument();

    const { menu } = await openAccountMenu();
    expect(within(menu).getByText('Dong Man')).toBeInTheDocument();
    expect(
      within(menu).getByText(`Riverside FC · ${role[0]?.toUpperCase()}${role.slice(1)}`),
    ).toBeInTheDocument();
    expect(!!within(menu).queryByRole('menuitem', { name: 'Organization' })).toBe(expected.org);
    expect(!!within(menu).queryByRole('menuitem', { name: 'Billing & usage' })).toBe(
      expected.billing,
    );
    expect(within(menu).getByRole('menuitem', { name: 'Profile' })).toBeInTheDocument();
    // Single membership → no switcher.
    expect(within(menu).queryByRole('menuitem', { name: /Switch organization/ })).toBeNull();
  });

  it('☰ menu (< 1024 px) reaches Library and Exports', async () => {
    const user = userEvent.setup();
    const { router } = renderApp('/library', { auth: testSession() });
    await screen.findByRole('heading', { name: 'Library' });
    await user.click(screen.getByRole('button', { name: 'Menu' }));
    await user.click(await screen.findByRole('menuitem', { name: 'Exports' }));
    await waitFor(() => expect(router.state.location.pathname).toBe('/exports'));
  });

  it('viewer: the N shortcut does not open New analysis', async () => {
    const user = userEvent.setup();
    const { router } = renderApp('/library', { auth: testSession({ role: 'viewer' }) });
    await screen.findByRole('heading', { name: 'Library' });
    await user.keyboard('n');
    expect(router.state.location.search).toEqual({});
    expect(screen.queryByRole('dialog')).not.toBeInTheDocument();
  });
});

describe('top bar G — organizations and sign out', () => {
  function seedTwoOrgs() {
    const me = mockUser({ email: 'dm@riversidefc.com', name: 'Dong Man' });
    mock.db.users.push(me);
    mock.db.orgs.push(mockOrg({ id: 'org-a', name: 'Riverside FC', region: 'eu' }));
    mock.db.orgs.push(mockOrg({ id: 'org-b', name: 'City Academy', region: 'us' }));
    mock.db.memberships.push({ user_id: me.id, org_id: 'org-a', role: 'owner' });
    mock.db.memberships.push({ user_id: me.id, org_id: 'org-b', role: 'viewer' });
    mock.db.cookie = { userId: me.id, activeOrgId: 'org-a' };
  }

  it('switches organization via refresh {org_id} and reloads into the new org', async () => {
    seedTwoOrgs();
    const { router } = renderApp('/exports', { auth: 'bootstrap' });
    await screen.findByRole('heading', { name: 'Exports' });
    expect(screen.getByRole('link', { name: 'New analysis' })).toBeInTheDocument();

    const { user, menu } = await openAccountMenu();
    // Keyboard path (jsdom has no pointer geometry for Radix's submenu pointer grace area).
    within(menu).getByRole('menuitem', { name: 'Switch organization' }).focus();
    await user.keyboard('{ArrowRight}');
    const item = await screen.findByRole('menuitem', { name: /City Academy/ });
    item.focus();
    await user.keyboard('{Enter}');

    await waitFor(() => expect(router.state.location.pathname).toBe('/library'));
    expect(mock.db.log).toContain('POST /api/v1/auth/refresh {"org_id":"org-b"}');
    expect(await screen.findByText('Switched to City Academy.')).toBeInTheDocument();
    // Viewer in the new org: New analysis disappears.
    await waitFor(() =>
      expect(screen.queryByRole('link', { name: 'New analysis' })).not.toBeInTheDocument(),
    );
  });

  it('sign out revokes the session and returns to sign in', async () => {
    seedTwoOrgs();
    const { router } = renderApp('/library', { auth: 'bootstrap' });
    const { user, menu } = await openAccountMenu();
    await user.click(within(menu).getByRole('menuitem', { name: 'Sign out' }));
    await waitFor(() => expect(router.state.location.pathname).toBe('/signin'));
    expect(mock.db.log).toContain('POST /api/v1/auth/logout');
    // The app is protected again.
    await router.navigate({ to: '/library' });
    await waitFor(() => expect(router.state.location.pathname).toBe('/signin'));
  });

  it('asks first (DLG-03) while an upload is running', async () => {
    seedTwoOrgs();
    useSignOutGuard.setState({ uploadsInProgress: 1 });
    const { router } = renderApp('/library', { auth: 'bootstrap' });
    const { user, menu } = await openAccountMenu();
    await user.click(within(menu).getByRole('menuitem', { name: 'Sign out' }));
    const dialog = await screen.findByRole('dialog', { name: 'An upload is in progress' });
    await user.click(within(dialog).getByRole('button', { name: 'Stay' }));
    expect(router.state.location.pathname).toBe('/library');
    expect(mock.db.log).not.toContain('POST /api/v1/auth/logout');
    useSignOutGuard.setState({ uploadsInProgress: 0 });
  });
});
