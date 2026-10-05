import { screen, waitFor } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { http, HttpResponse } from 'msw';
import { mockOrg, mockUser } from '@/mocks/identity';
import { setupIdentityMock } from '@/test/msw';
import { renderApp } from '@/test/renderApp';

const mock = setupIdentityMock();

function seedInvite(expired = false) {
  mock.db.orgs.push(mockOrg({ id: 'org-1', name: 'Riverside FC' }));
  mock.db.invites.push({
    token: 'tok',
    org_id: 'org-1',
    email: 'jordan@riversidefc.com',
    role: 'editor',
    inviter_name: 'Sam Reed',
    expired,
  });
}

function signedInAs(email: string) {
  const user = mockUser({ email });
  mock.db.users.push(user);
  mock.db.cookie = { userId: user.id, activeOrgId: null };
  return user;
}

describe('SCR-52 Accept invitation', () => {
  it('signed out → accept leads to sign up with the email locked', async () => {
    seedInvite();
    const user = userEvent.setup();
    const { router } = renderApp('/invite/tok', { auth: null });
    expect(
      await screen.findByRole('heading', { name: 'Sam Reed invited you to join Riverside FC' }),
    ).toBeInTheDocument();
    expect(screen.getByText('Editor').parentElement).toHaveTextContent(
      'as Editor · data stored in the European Union',
    );
    await user.click(screen.getByRole('link', { name: 'Accept invitation' }));

    await waitFor(() => expect(router.state.location.pathname).toBe('/signup'));
    const email = await screen.findByLabelText('Work email');
    await waitFor(() => expect(email).toHaveValue('jordan@riversidefc.com'));
    expect(email).toHaveAttribute('readonly');
    expect(screen.getByText(/You're joining/)).toHaveTextContent(
      "You're joining Riverside FC as Editor.",
    );
  });

  it('signed in with the invited email → accept joins the org', async () => {
    seedInvite();
    signedInAs('jordan@riversidefc.com');
    const user = userEvent.setup();
    const { router } = renderApp('/invite/tok', { auth: 'bootstrap' });
    await user.click(await screen.findByRole('button', { name: 'Accept invitation' }));
    await waitFor(() => expect(router.state.location.pathname).toBe('/library'));
    expect(mock.db.memberships).toEqual([
      expect.objectContaining({ org_id: 'org-1', role: 'editor' }),
    ]);
    expect(await screen.findByText('You joined Riverside FC.')).toBeInTheDocument();
  });

  it('signed in with another email → message + sign in with another account', async () => {
    seedInvite();
    signedInAs('someone@else.com');
    const user = userEvent.setup();
    const { router } = renderApp('/invite/tok', { auth: 'bootstrap' });
    const text = await screen.findByText(/This invitation was sent to/);
    expect(text.closest('[role="status"]')).toHaveTextContent(
      "This invitation was sent to jordan@riversidefc.com, but you're signed in as someone@else.com.",
    );
    expect(screen.queryByRole('button', { name: 'Accept invitation' })).not.toBeInTheDocument();
    await user.click(screen.getByRole('button', { name: 'Sign in with another account' }));
    await waitFor(() => expect(router.state.location.pathname).toBe('/signin'));
    expect(router.state.location.search).toEqual({ return_to: '/invite/tok' });
    expect(mock.db.log).toContain('POST /api/v1/auth/logout');
    expect(mock.db.cookie).toBeNull();
  });

  it('expired invitation', async () => {
    seedInvite(true);
    renderApp('/invite/tok', { auth: null });
    expect(
      await screen.findByRole('heading', { name: 'This invitation has expired' }),
    ).toBeInTheDocument();
    expect(screen.getByText('Ask Sam Reed to send you a new invite.')).toBeInTheDocument();
    expect(screen.queryByRole('button', { name: 'Accept invitation' })).not.toBeInTheDocument();
  });

  it('410 (used / expired invite) shows the expired state', async () => {
    mock.server.use(
      http.get('*/api/v1/invites/:token', () =>
        HttpResponse.json(
          { type: 'about:blank', title: 'Gone', status: 410, code: 'TOKEN_EXPIRED' },
          { status: 410 },
        ),
      ),
    );
    renderApp('/invite/used', { auth: null });
    expect(
      await screen.findByRole('heading', { name: 'This invitation has expired' }),
    ).toBeInTheDocument();
  });

  it('unknown token', async () => {
    renderApp('/invite/nope', { auth: null });
    expect(
      await screen.findByRole('heading', { name: "This invitation isn't valid" }),
    ).toBeInTheDocument();
  });
});
