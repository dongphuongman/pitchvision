import { screen, waitFor } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { mockOrg, mockUser } from '@/mocks/identity';
import { suggestRegion } from '@/lib/region';
import { setupIdentityMock } from '@/test/msw';
import { renderApp } from '@/test/renderApp';

const mock = setupIdentityMock();

describe('SCR-07 Onboarding', () => {
  it('3 steps: creates the org with the confirmed region, switches to it, finishes', async () => {
    const user = userEvent.setup();
    const me = mockUser({ email: 'solo@club.com' });
    mock.db.users.push(me);
    mock.db.cookie = { userId: me.id, activeOrgId: null };

    const { router } = renderApp('/exports', { auth: 'bootstrap' });
    // No org → wizard, return_to kept.
    expect(
      await screen.findByRole('heading', { name: 'Set up your organization' }),
    ).toBeInTheDocument();
    expect(router.state.location.pathname).toBe('/onboarding/organization');
    expect(screen.getByText('Step 1 of 3')).toBeInTheDocument();

    // Step 1: name is required.
    await user.click(screen.getByRole('button', { name: 'Continue' }));
    expect(await screen.findByText("Enter your organization's name.")).toBeInTheDocument();
    await user.type(screen.getByLabelText('Organization name'), 'Riverside FC Academy');
    await user.click(screen.getByRole('radio', { name: 'Academy' }));
    await user.click(screen.getByRole('button', { name: 'Continue' }));

    // Step 2: region (heading receives focus on step change).
    const regionHeading = await screen.findByRole('heading', {
      name: 'Where should we store your data?',
    });
    await waitFor(() => expect(regionHeading).toHaveFocus());
    await user.click(screen.getByRole('radio', { name: /European Union/ }));
    await user.click(screen.getByRole('button', { name: 'Continue' }));
    // Small confirm because the region is permanent; nothing created yet.
    expect(
      await screen.findByText('Store data in the European Union? This is permanent.'),
    ).toBeInTheDocument();
    expect(mock.db.orgs).toHaveLength(0);
    await user.click(screen.getByRole('button', { name: 'Confirm' }));

    // Step 3: preferences; the org exists and the session switched to it.
    expect(await screen.findByRole('heading', { name: 'Preferences' })).toBeInTheDocument();
    expect(mock.db.orgs).toEqual([
      expect.objectContaining({ name: 'Riverside FC Academy', kind: 'academy', region: 'eu' }),
    ]);
    const createIdx = mock.db.log.findIndex((l) => l.startsWith('POST /api/v1/orgs '));
    const switchIdx = mock.db.log.findIndex(
      (l) => l === 'POST /api/v1/auth/refresh {"org_id":"org-1"}',
    );
    expect(createIdx).toBeGreaterThanOrEqual(0);
    expect(switchIdx).toBeGreaterThan(createIdx);
    expect(screen.getByDisplayValue(/European Union · Frankfurt/)).toBeInTheDocument();

    // Back to step 2 shows the region read-only.
    await user.click(screen.getByRole('button', { name: 'Back' }));
    expect(await screen.findByText(/This can't be changed\./)).toBeInTheDocument();
    expect(screen.getByRole('radio', { name: /European Union/ })).toBeDisabled();
    await user.click(screen.getByRole('button', { name: 'Continue' }));

    await user.click(await screen.findByRole('radio', { name: 'Imperial (yd, mph)' }));
    await user.click(screen.getByRole('button', { name: 'Finish setup' }));
    await waitFor(() => expect(router.state.location.pathname).toBe('/exports'));
    expect(mock.db.orgs).toHaveLength(1);
  });

  it('region is mandatory', async () => {
    const user = userEvent.setup();
    const me = mockUser({ email: 'solo@club.com' });
    mock.db.users.push(me);
    mock.db.cookie = { userId: me.id, activeOrgId: null };
    renderApp('/onboarding/organization', { auth: 'bootstrap' });
    await user.type(await screen.findByLabelText('Organization name'), 'X FC');
    await user.click(screen.getByRole('button', { name: 'Continue' }));
    await screen.findByRole('heading', { name: 'Where should we store your data?' });
    // Clear any browser-based suggestion to test the empty case.
    const { useOnboardingDraft } = await import('./draft');
    useOnboardingDraft.getState().set({ region: null });
    await user.click(await screen.findByRole('button', { name: 'Continue' }));
    expect(await screen.findByText('Choose where to store your data.')).toBeInTheDocument();
  });

  it('members of an org skip the wizard', async () => {
    const me = mockUser({ email: 'member@club.com' });
    mock.db.users.push(me);
    mock.db.orgs.push(mockOrg({ id: 'org-9' }));
    mock.db.memberships.push({ user_id: me.id, org_id: 'org-9', role: 'editor' });
    mock.db.cookie = { userId: me.id, activeOrgId: 'org-9' };
    const { router } = renderApp('/onboarding/organization', { auth: 'bootstrap' });
    await waitFor(() => expect(router.state.location.pathname).toBe('/library'));
  });
});

describe('suggestRegion', () => {
  it('uses the time zone, then the language region', () => {
    expect(suggestRegion('Europe/Berlin', [])).toBe('eu');
    expect(suggestRegion('America/New_York', [])).toBe('us');
    expect(suggestRegion('Asia/Tokyo', ['de-DE'])).toBe('eu');
    expect(suggestRegion('Asia/Tokyo', ['en-US'])).toBe('us');
    expect(suggestRegion('Asia/Tokyo', ['ja'])).toBeNull();
  });
});
