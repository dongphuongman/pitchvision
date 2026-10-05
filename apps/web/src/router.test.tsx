import { act, screen, waitFor, within } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { renderApp } from '@/test/renderApp';

describe('router + app shell', () => {
  it('redirects / to the library and renders the shell', async () => {
    const { router } = renderApp('/');
    expect(await screen.findByRole('heading', { level: 1, name: 'Library' })).toBeInTheDocument();
    expect(router.state.location.pathname).toBe('/library');

    const nav = screen.getByRole('navigation', { name: 'Main navigation' });
    expect(within(nav).getByRole('link', { name: 'Library' })).toHaveAttribute(
      'aria-current',
      'page',
    );
    expect(within(nav).getByRole('link', { name: 'Exports' })).toBeInTheDocument();
    // Phase C: no Live entry in the top bar.
    expect(within(nav).queryByRole('link', { name: /live/i })).not.toBeInTheDocument();
    expect(screen.getByRole('link', { name: 'New analysis' })).toBeInTheDocument();
  });

  it('renders the workspace for /videos/:id and defaults to the overview tab', async () => {
    const { router } = renderApp('/videos/abc123');
    expect(await screen.findByRole('tab', { name: 'Overview' })).toHaveAttribute(
      'aria-selected',
      'true',
    );
    expect(router.state.location.pathname).toBe('/videos/abc123/overview');
  });

  it('shows 404 for unknown routes and unknown settings sections', async () => {
    renderApp('/nope');
    expect(await screen.findByText('Page not found')).toBeInTheDocument();
  });

  it('404s unknown settings sections', async () => {
    renderApp('/settings/bogus');
    expect(await screen.findByText('Page not found')).toBeInTheDocument();
  });

  it('settings → about shows API meta via useMeta()', async () => {
    const fetchMock = vi.fn((req: Request) =>
      Promise.resolve(
        req.url.endsWith('/api/v1/meta')
          ? new Response(JSON.stringify({ version: '1.2.3', schema_version: 4 }), {
              status: 200,
              headers: { 'Content-Type': 'application/json' },
            })
          : new Response(null, { status: 404 }),
      ),
    );
    vi.stubGlobal('fetch', fetchMock);
    renderApp('/settings/about');
    expect(await screen.findByText('1.2.3')).toBeInTheDocument();
    expect(screen.getByText('4')).toBeInTheDocument();
    vi.unstubAllGlobals();
  });

  it('settings → about surfaces a problem+json error by code', async () => {
    vi.stubGlobal(
      'fetch',
      vi.fn(() =>
        Promise.resolve(
          new Response(
            JSON.stringify({ type: 'about:blank', title: 'x', status: 503, code: 'INTERNAL' }),
            { status: 503, headers: { 'Content-Type': 'application/problem+json' } },
          ),
        ),
      ),
    );
    const { queryClient } = renderApp('/settings/about');
    queryClient.setDefaultOptions({ queries: { retry: false } });
    expect(await screen.findByRole('alert')).toHaveTextContent('Something went wrong on our side.');
    vi.unstubAllGlobals();
  });

  it('renders the sign-in page without the app shell', async () => {
    renderApp('/signin');
    expect(await screen.findByRole('heading', { name: 'Sign in' })).toBeInTheDocument();
    expect(screen.queryByRole('navigation', { name: 'Main navigation' })).not.toBeInTheDocument();
  });
});

describe('dialogs addressed by query string', () => {
  it('opens New analysis from the top bar, and browser Back closes it', async () => {
    const user = userEvent.setup();
    const { router } = renderApp('/library');
    await user.click(await screen.findByRole('link', { name: 'New analysis' }));

    const dialog = await screen.findByRole('dialog', { name: 'New analysis' });
    expect(router.state.location.search).toEqual({ new: 'youtube' });
    expect(within(dialog).getByRole('tab', { name: 'YouTube' })).toHaveAttribute(
      'aria-selected',
      'true',
    );

    act(() => router.history.back());
    await waitFor(() => expect(screen.queryByRole('dialog')).not.toBeInTheDocument());
    expect(router.state.location.search).toEqual({});
  });

  it('deep link opens the right tab; closing removes the param without leaving the app', async () => {
    const user = userEvent.setup();
    const { router } = renderApp('/library?new=upload');
    const dialog = await screen.findByRole('dialog', { name: 'New analysis' });
    expect(within(dialog).getByRole('tab', { name: 'Upload' })).toHaveAttribute(
      'aria-selected',
      'true',
    );

    // Switching tab rewrites the param in place.
    await user.click(within(dialog).getByRole('tab', { name: 'YouTube' }));
    await waitFor(() => expect(router.state.location.search).toEqual({ new: 'youtube' }));

    await user.click(within(dialog).getByRole('button', { name: 'Close' }));
    await waitFor(() => expect(screen.queryByRole('dialog')).not.toBeInTheDocument());
    expect(router.state.location.pathname).toBe('/library');
    expect(router.state.location.search).toEqual({});
  });

  it('opens the app-wide shortcuts dialog with "?" and closes it with Escape', async () => {
    const user = userEvent.setup();
    const { router } = renderApp('/exports');
    await screen.findByRole('heading', { name: 'Exports' });
    await user.keyboard('?');
    expect(await screen.findByRole('dialog', { name: 'Keyboard shortcuts' })).toBeInTheDocument();
    expect(router.state.location.search).toEqual({ dialog: 'shortcuts' });
    await user.keyboard('{Escape}');
    await waitFor(() => expect(screen.queryByRole('dialog')).not.toBeInTheDocument());
    expect(router.state.location.search).toEqual({});
  });

  it('ignores invalid dialog values', async () => {
    renderApp('/library?new=live');
    await screen.findByRole('heading', { name: 'Library' });
    expect(screen.queryByRole('dialog')).not.toBeInTheDocument();
  });
});
