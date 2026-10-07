import { act, screen, waitFor, within } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { http, HttpResponse } from 'msw';
import { mockOrg, mockUser } from '@/mocks/identity';
import { setupIdentityMock } from '@/test/msw';
import { renderApp } from '@/test/renderApp';
import { VERIFY_POLL_MS } from './VerifyEmailPage';

const PASSWORD = 'correct horse battery';
const mock = setupIdentityMock();

function seedMember(email = 'dm@riversidefc.com', verified = true) {
  const user = mockUser({ email, email_verified: verified });
  mock.db.users.push(user);
  mock.db.orgs.push(mockOrg({ id: 'org-1' }));
  mock.db.memberships.push({ user_id: user.id, org_id: 'org-1', role: 'owner' });
  return user;
}

async function signIn(email: string, password = PASSWORD) {
  const user = userEvent.setup();
  await user.type(await screen.findByLabelText('Email'), email);
  await user.type(screen.getByLabelText('Password'), password);
  await user.click(screen.getByRole('button', { name: 'Sign in' }));
  return user;
}

describe('SCR-02 Sign in', () => {
  it('signs in and opens the library', async () => {
    seedMember();
    const { router } = renderApp('/signin', { auth: null });
    await signIn('dm@riversidefc.com');
    await waitFor(() => expect(router.state.location.pathname).toBe('/library'));
    expect(await screen.findByRole('heading', { name: 'Library' })).toBeInTheDocument();
  });

  it('goes to return_to after sign-in', async () => {
    seedMember();
    const { router } = renderApp('/signin?return_to=%2Fexports', { auth: null });
    await signIn('dm@riversidefc.com');
    await waitFor(() => expect(router.state.location.pathname).toBe('/exports'));
  });

  it('ignores an off-site return_to (open redirect)', async () => {
    seedMember();
    const { router } = renderApp(
      `/signin?return_to=${encodeURIComponent('https://evil.com/phish')}`,
      { auth: null },
    );
    await signIn('dm@riversidefc.com');
    await waitFor(() => expect(router.state.location.pathname).toBe('/library'));
  });

  it('unverified email → SCR-06 verify email', async () => {
    mock.db.users.push(mockUser({ email: 'new@club.com', email_verified: false }));
    const { router } = renderApp('/signin?return_to=%2Fexports', { auth: null });
    await signIn('new@club.com');
    await waitFor(() => expect(router.state.location.pathname).toBe('/verify-email'));
    expect(router.state.location.search).toEqual({ return_to: '/exports' });
    expect(await screen.findByRole('heading', { name: 'Check your inbox' })).toBeInTheDocument();
  });

  it('no organization → SCR-07 onboarding', async () => {
    mock.db.users.push(mockUser({ email: 'solo@club.com' }));
    const { router } = renderApp('/signin', { auth: null });
    await signIn('solo@club.com');
    await waitFor(() => expect(router.state.location.pathname).toBe('/onboarding/organization'));
  });

  it('shows INVALID_CREDENTIALS inline and keeps the email', async () => {
    seedMember();
    renderApp('/signin', { auth: null });
    await signIn('dm@riversidefc.com', 'wrong password!');
    expect(await screen.findByRole('alert')).toHaveTextContent('Incorrect email or password.');
    expect(screen.getByLabelText('Email')).toHaveValue('dm@riversidefc.com');
    expect(screen.getByLabelText('Password')).toHaveValue('');
  });

  it('validates the email format before calling the API', async () => {
    renderApp('/signin', { auth: null });
    await signIn('not-an-email');
    expect(await screen.findByText(/Enter a valid email address/)).toBeInTheDocument();
    expect(mock.db.log).toEqual([]);
  });

  it('links to Google sign-in with the sanitised return_to', async () => {
    renderApp('/signin?return_to=%2Fexports', { auth: null });
    expect(await screen.findByRole('link', { name: 'Continue with Google' })).toHaveAttribute(
      'href',
      '/api/v1/auth/oidc/google/start?return_to=%2Fexports',
    );
  });

  it('hides Google when the server has it off', async () => {
    mock.db.googleEnabled = false;
    let answered = false;
    mock.server.events.on('response:mocked', ({ request }) => {
      if (request.url.endsWith('/api/v1/auth/providers')) answered = true;
    });
    renderApp('/signin', { auth: null });
    await screen.findByRole('button', { name: 'Sign in' });
    await waitFor(() => expect(answered).toBe(true));
    expect(screen.queryByRole('link', { name: 'Continue with Google' })).not.toBeInTheDocument();
    mock.server.events.removeAllListeners();
  });
});

describe('SCR-03 Sign up', () => {
  it('requires the terms checkbox and validates name, email and password', async () => {
    const user = userEvent.setup();
    renderApp('/signup', { auth: null });
    const submit = await screen.findByRole('button', { name: 'Create account' });
    expect(submit).toBeDisabled();
    await user.click(screen.getByRole('checkbox', { name: /I agree to the Terms/ }));
    expect(submit).toBeEnabled();

    await user.type(screen.getByLabelText('Work email'), 'bad@');
    await user.type(screen.getByLabelText('Password'), 'short');
    await user.click(submit);
    expect(await screen.findByText('Enter your name.')).toBeInTheDocument();
    expect(screen.getByText(/Enter a valid email address/)).toBeInTheDocument();
    expect(screen.getByText('Use at least 10 characters.')).toBeInTheDocument();
    expect(screen.getByLabelText('Full name')).toHaveFocus();
    expect(mock.db.log).toEqual([]);
  });

  it('shows the strength level for the password', async () => {
    const user = userEvent.setup();
    renderApp('/signup', { auth: null });
    const pw = await screen.findByLabelText('Password');
    expect(screen.getByText(/Too short · at least 10 characters/)).toBeInTheDocument();
    await user.type(pw, 'Correct-Horse-9-Battery');
    expect(screen.getByText(/Strong · at least 10 characters/)).toBeInTheDocument();
  });

  it('EMAIL_TAKEN → inline error with "Sign in instead"', async () => {
    seedMember('taken@club.com');
    const user = userEvent.setup();
    renderApp('/signup?return_to=%2Fexports', { auth: null });
    await user.type(await screen.findByLabelText('Full name'), 'Sam Reed');
    await user.type(screen.getByLabelText('Work email'), 'taken@club.com');
    await user.type(screen.getByLabelText('Password'), PASSWORD);
    await user.click(screen.getByRole('checkbox', { name: /I agree/ }));
    await user.click(screen.getByRole('button', { name: 'Create account' }));

    const alert = await screen.findByText(/An account with this email already exists/);
    const link = within(alert).getByRole('link', { name: 'Sign in instead' });
    expect(link.getAttribute('href')).toContain('/signin?');
    expect(link.getAttribute('href')).toContain('return_to=%2Fexports');
    expect(screen.getByLabelText('Work email')).toHaveAttribute('aria-invalid', 'true');
  });

  it('creates the account and continues to verify email', async () => {
    const user = userEvent.setup();
    const { router } = renderApp('/signup', { auth: null });
    await user.type(await screen.findByLabelText('Full name'), 'Sam Reed');
    await user.type(screen.getByLabelText('Work email'), 'sam@club.com');
    await user.type(screen.getByLabelText('Password'), PASSWORD);
    await user.click(screen.getByRole('checkbox', { name: /I agree/ }));
    await user.click(screen.getByRole('button', { name: 'Create account' }));
    await waitFor(() => expect(router.state.location.pathname).toBe('/verify-email'));
    expect(await screen.findByText('sam@club.com')).toBeInTheDocument();
  });
});

describe('SCR-04 Forgot password', () => {
  it('always shows the same "if an account exists" message', async () => {
    seedMember('known@club.com');
    for (const email of ['known@club.com', 'nobody@club.com']) {
      const user = userEvent.setup();
      const view = renderApp(`/forgot-password?email=${encodeURIComponent(email)}`, {
        auth: null,
      });
      expect(await screen.findByLabelText('Email')).toHaveValue(email);
      await user.click(screen.getByRole('button', { name: 'Send reset link' }));
      expect(await screen.findByRole('heading', { name: 'Check your inbox' })).toHaveFocus();
      expect(screen.getByRole('status')).toHaveTextContent(
        `If an account exists for ${email}, we sent a link to reset your password.`,
      );
      expect(screen.getByText(/Resend in 1:00|Resend in 0:59/)).toBeInTheDocument();
      view.unmount();
    }
    expect(
      mock.db.log.filter((l) => l.startsWith('POST /api/v1/auth/password/forgot')),
    ).toHaveLength(2);
  });
});

describe('SCR-05 Reset password', () => {
  it('expired / used token → "Request a new link"', async () => {
    mock.db.resetTokens.old = 'expired';
    const user = userEvent.setup();
    renderApp('/reset-password?token=old', { auth: null });
    await user.type(await screen.findByLabelText('New password'), PASSWORD);
    await user.type(screen.getByLabelText('Confirm password'), PASSWORD);
    await user.click(screen.getByRole('button', { name: 'Update password' }));
    expect(await screen.findByRole('heading', { name: 'Link expired' })).toBeInTheDocument();
    expect(screen.getByRole('link', { name: 'Request a new link' })).toHaveAttribute(
      'href',
      '/forgot-password',
    );
  });

  it('missing token shows the expired state directly', async () => {
    renderApp('/reset-password', { auth: null });
    expect(await screen.findByRole('heading', { name: 'Link expired' })).toBeInTheDocument();
  });

  it('mismatching confirmation is an inline error', async () => {
    mock.db.resetTokens.good = 'valid';
    const user = userEvent.setup();
    renderApp('/reset-password?token=good', { auth: null });
    await user.type(await screen.findByLabelText('New password'), PASSWORD);
    await user.type(screen.getByLabelText('Confirm password'), `${PASSWORD}x`);
    await user.click(screen.getByRole('button', { name: 'Update password' }));
    expect(await screen.findByText("Passwords don't match.")).toBeInTheDocument();
    expect(mock.db.log).toEqual([]);
  });

  it('valid token → password updated, back to sign in', async () => {
    mock.db.resetTokens.good = 'valid';
    const user = userEvent.setup();
    const { router } = renderApp('/reset-password?token=good', { auth: null });
    await user.type(await screen.findByLabelText('New password'), PASSWORD);
    await user.type(screen.getByLabelText('Confirm password'), PASSWORD);
    await user.click(screen.getByRole('button', { name: 'Update password' }));
    await waitFor(() => expect(router.state.location.pathname).toBe('/signin'));
    expect(await screen.findByText('Password updated. Please sign in.')).toBeInTheDocument();
  });
});

describe('SCR-06 Verify email', () => {
  afterEach(() => {
    vi.useRealTimers();
  });

  it('polls every 5 s and continues once verified on another device', async () => {
    vi.useFakeTimers({ shouldAdvanceTime: true });
    const user = mockUser({ email: 'new@club.com', email_verified: false });
    mock.db.users.push(user);
    mock.db.cookie = { userId: user.id, activeOrgId: null };

    const { router } = renderApp('/verify-email', { auth: 'bootstrap' });
    expect(await screen.findByRole('heading', { name: 'Check your inbox' })).toBeInTheDocument();
    expect(screen.getByText('new@club.com')).toBeInTheDocument();
    await waitFor(() => expect(router.state.location.pathname).toBe('/verify-email'));

    // Still unverified after one poll.
    await act(() => vi.advanceTimersByTimeAsync(VERIFY_POLL_MS));
    expect(router.state.location.pathname).toBe('/verify-email');

    // The link is opened elsewhere → next poll sees it, refreshes the token, moves on.
    user.email_verified = true;
    await act(() => vi.advanceTimersByTimeAsync(VERIFY_POLL_MS));
    await waitFor(() => expect(router.state.location.pathname).toBe('/onboarding/organization'));
  });

  it('opening the email link verifies the token and continues', async () => {
    const user = mockUser({ email: 'new@club.com', email_verified: false });
    mock.db.users.push(user);
    mock.db.verifyTokens.tok123 = user.id;
    const { router } = renderApp('/verify-email?token=tok123', { auth: 'bootstrap' });
    await waitFor(() => expect(router.state.location.pathname).toBe('/onboarding/organization'));
  });

  it('"Change it" deletes the unverified account and returns to sign up with the name', async () => {
    const u = userEvent.setup();
    const user = mockUser({ email: 'typo@club.con', name: 'Sam Reed', email_verified: false });
    mock.db.users.push(user);
    mock.db.cookie = { userId: user.id, activeOrgId: null };
    const { router } = renderApp('/verify-email', { auth: 'bootstrap' });
    await u.click(await screen.findByRole('button', { name: 'Change it' }));
    await waitFor(() => expect(router.state.location.pathname).toBe('/signup'));
    expect(mock.db.users).toHaveLength(0);
    expect(await screen.findByLabelText('Full name')).toHaveValue('Sam Reed');
  });

  it('resend starts a 60 s cooldown', async () => {
    const u = userEvent.setup();
    const user = mockUser({ email: 'new@gmail.com', email_verified: false });
    mock.db.users.push(user);
    mock.db.cookie = { userId: user.id, activeOrgId: null };
    renderApp('/verify-email', { auth: 'bootstrap' });
    expect(await screen.findByRole('link', { name: 'Open Gmail' })).toBeInTheDocument();
    await u.click(screen.getByRole('button', { name: 'Resend email' }));
    expect(await screen.findByText(/Resend in (1:00|0:59)/)).toBeInTheDocument();
    expect(mock.db.log).toContain('POST /api/v1/auth/verify-email/resend');
  });
});

describe('Google OIDC /auth/callback', () => {
  it('status=ok → refresh with the cookie, then return_to', async () => {
    const user = seedMember();
    mock.db.cookie = { userId: user.id, activeOrgId: 'org-1' };
    const { router } = renderApp('/auth/callback#status=ok&new_user=false&return_to=%2Fexports', {
      auth: 'bootstrap',
    });
    await waitFor(() => expect(router.state.location.pathname).toBe('/exports'));
    expect(mock.db.log).toContain('POST /api/v1/auth/refresh');
  });

  it('new user without an org → onboarding; off-site return_to ignored', async () => {
    const user = mockUser({ email: 'g@gmail.com' });
    mock.db.users.push(user);
    mock.db.cookie = { userId: user.id, activeOrgId: null };
    const { router } = renderApp(
      '/auth/callback#status=ok&new_user=true&return_to=https%3A%2F%2Fevil.com',
      { auth: 'bootstrap' },
    );
    await waitFor(() => expect(router.state.location.pathname).toBe('/onboarding/organization'));
    expect(router.state.location.search).toEqual({});
  });

  it('#error=EMAIL_NOT_VERIFIED → failure state', async () => {
    renderApp('/auth/callback#error=EMAIL_NOT_VERIFIED', { auth: 'bootstrap' });
    expect(
      await screen.findByRole('heading', { name: "We couldn't sign you in with Google" }),
    ).toBeInTheDocument();
    expect(screen.getByText('Please verify your email address first.')).toBeInTheDocument();
  });

  it('#error=GOOGLE_UNAVAILABLE explains that Google is not set up', async () => {
    renderApp('/auth/callback#error=GOOGLE_UNAVAILABLE', { auth: 'bootstrap' });
    expect(
      await screen.findByText(/Google sign-in isn't set up on this server/),
    ).toBeInTheDocument();
  });
});

describe('rate limiting', () => {
  it('429 with Retry-After locks the sign-in form with a countdown', async () => {
    mock.server.use(
      http.post('*/api/v1/auth/login', () =>
        HttpResponse.json(
          { type: 'about:blank', title: 'x', status: 429, code: 'RATE_LIMITED' },
          { status: 429, headers: { 'Retry-After': '900' } },
        ),
      ),
    );
    renderApp('/signin', { auth: null });
    await signIn('dm@riversidefc.com');
    expect(await screen.findByRole('alert')).toHaveTextContent(
      /Too many attempts\. Try again in 1[45]:\d\d\./,
    );
    expect(screen.getByRole('button', { name: 'Sign in' })).toBeDisabled();
  });
});
