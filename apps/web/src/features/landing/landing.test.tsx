import { screen, waitFor, within } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { rememberSignupPlan, takeSignupPlan } from '@/lib/signupPlan';
import { setupIdentityMock } from '@/test/msw';
import { renderApp, testSession } from '@/test/renderApp';

setupIdentityMock();

describe('SCR-01 Landing', () => {
  it('shows the pitch, features and pricing to signed-out visitors', async () => {
    renderApp('/', { auth: null });
    expect(
      await screen.findByRole('heading', { level: 1, name: 'Analyze any broadcast match.' }),
    ).toBeInTheDocument();
    expect(screen.getByRole('link', { name: 'Analyze a match free' })).toHaveAttribute(
      'href',
      `/signup?return_to=${encodeURIComponent('/library?new=upload')}`,
    );
    expect(screen.getByRole('link', { name: 'Get started' })).toHaveAttribute('href', '/signup');
    expect(screen.getByRole('link', { name: 'Sign in' })).toHaveAttribute('href', '/signin');
    expect(screen.getByRole('link', { name: 'Features' })).toHaveAttribute('href', '#features');
    const pro = screen.getByRole('listitem', { name: 'Pro' });
    expect(within(pro).getByText('900 min/mo')).toBeInTheDocument();
    expect(within(pro).getByRole('link', { name: 'Choose Pro' })).toHaveAttribute(
      'href',
      '/signup?plan=pro',
    );
    expect(screen.getByRole('link', { name: 'Terms' })).toHaveAttribute('href', '/terms');
    expect(screen.getByRole('img', { name: /broadcast frame with tracked players/ })).toBeVisible();
  });

  it('sends signed-in users to the library', async () => {
    const { router } = renderApp('/', { auth: testSession() });
    await waitFor(() => expect(router.state.location.pathname).toBe('/library'));
  });
});

describe('SCR-50', () => {
  it('/403 explains the missing role and links back', async () => {
    const user = userEvent.setup();
    const { router } = renderApp('/403', { auth: testSession({ role: 'viewer' }) });
    expect(await screen.findByText("You don't have access")).toBeInTheDocument();
    await user.click(screen.getByRole('link', { name: 'Go to library' }));
    await waitFor(() => expect(router.state.location.pathname).toBe('/library'));
  });
});

describe('sign-up plan memory', () => {
  it('keeps pro / team once, ignores free', () => {
    rememberSignupPlan('pro');
    expect(takeSignupPlan()).toBe('pro');
    expect(takeSignupPlan()).toBeNull();
    rememberSignupPlan('free');
    expect(takeSignupPlan()).toBeNull();
  });
});
