import type { QueryClient } from '@tanstack/react-query';
import {
  createRootRouteWithContext,
  createRoute,
  createRouter,
  lazyRouteComponent,
  notFound,
  redirect,
  type RouterHistory,
} from '@tanstack/react-router';
import { AppShell } from '@/app/AppShell';
import { RootLayout } from '@/app/RootLayout';
import { ErrorPage, FullPageSpinner, NotFoundPage } from '@/app/StatusPages';
import { nextAfterAuth, safeReturnTo } from '@/auth/redirects';
import { ensureSession, switchOrganization, useAuthStore } from '@/auth/session';
import {
  ONBOARDING_STEPS,
  useOnboardingDraft,
  type OnboardingStep,
} from '@/features/onboarding/draft';
import {
  SORTS,
  SOURCE_FILTERS,
  STATUS_FILTERS,
  type LibrarySort,
  type SourceFilter,
  type StatusFilter,
} from '@/features/library/model';
import { SETTINGS_SECTIONS, type SettingsSection } from '@/features/settings/sections';
import { WORKSPACE_TABS, type WorkspaceTab } from '@/features/video/tabs';

// Screens are code-split per route; the shell (top bar, dialogs host) stays in the entry chunk.
const LibraryPage = lazyRouteComponent(
  () => import('@/features/library/LibraryPage'),
  'LibraryPage',
);
const SignInPage = lazyRouteComponent(() => import('@/features/auth/SignInPage'), 'SignInPage');
const SignUpPage = lazyRouteComponent(() => import('@/features/auth/SignUpPage'), 'SignUpPage');
const ForgotPasswordPage = lazyRouteComponent(
  () => import('@/features/auth/ForgotPasswordPage'),
  'ForgotPasswordPage',
);
const ResetPasswordPage = lazyRouteComponent(
  () => import('@/features/auth/ResetPasswordPage'),
  'ResetPasswordPage',
);
const VerifyEmailPage = lazyRouteComponent(
  () => import('@/features/auth/VerifyEmailPage'),
  'VerifyEmailPage',
);
const AuthCallbackPage = lazyRouteComponent(
  () => import('@/features/auth/AuthCallbackPage'),
  'AuthCallbackPage',
);
const InvitePage = lazyRouteComponent(() => import('@/features/auth/InvitePage'), 'InvitePage');
const OnboardingPage = lazyRouteComponent(
  () => import('@/features/onboarding/OnboardingPage'),
  'OnboardingPage',
);
const LegalPage = lazyRouteComponent(() => import('@/features/legal/LegalPage'), 'LegalPage');
const ExportsPage = lazyRouteComponent(
  () => import('@/features/exports/ExportsPage'),
  'ExportsPage',
);
const SettingsPage = lazyRouteComponent(
  () => import('@/features/settings/SettingsPage'),
  'SettingsPage',
);
const ProcessingPage = lazyRouteComponent(
  () => import('@/features/video/ProcessingPage'),
  'ProcessingPage',
);
const WorkspacePage = lazyRouteComponent(
  () => import('@/features/video/WorkspacePage'),
  'WorkspacePage',
);
const TeamsPage = lazyRouteComponent(() => import('@/features/teams/TeamsPage'), 'TeamsPage');

/*
 * Code-based route tree (no codegen step). Route ids used with getRouteApi():
 *   /_app/library, /_app/videos/$videoId/$tab, /_app/videos/$videoId/teams,
 *   /_app/settings/$section, /signin …
 * Phase C screens (Live) are intentionally absent.
 *
 * Auth rules (UIUX §3.1, SCR-02 E05):
 *   - every navigation first waits for the one-time silent refresh (ensureSession);
 *   - app routes: signed out → /signin?return_to=<here>; unverified → /verify-email;
 *     no organization → /onboarding/organization; return_to is carried along;
 *   - signed-in users opening /signin or /signup go straight to their destination.
 */

export interface RouterContext {
  queryClient: QueryClient;
}

export type AppDialog = 'shortcuts';
export type NewAnalysisSource = 'youtube' | 'upload';

interface ReturnToSearch {
  return_to?: string;
}

function str(v: unknown): string | undefined {
  return typeof v === 'string' && v !== '' ? v : undefined;
}

/** Only same-origin relative paths survive (open-redirect guard). */
function returnToSearch(s: Record<string, unknown>): ReturnToSearch {
  return { return_to: safeReturnTo(s.return_to) };
}

/** Signed-in users don't need the public auth forms: send them where they belong. */
function redirectIfSignedIn(returnTo: string | undefined): void {
  const auth = useAuthStore.getState();
  if (auth.status === 'authenticated') throw redirect({ href: nextAfterAuth(auth, returnTo) });
}

function requireSignedIn(href: string) {
  const auth = useAuthStore.getState();
  if (auth.status !== 'authenticated' || !auth.user) {
    throw redirect({ to: '/signin', search: { return_to: safeReturnTo(href) } });
  }
  return { ...auth, user: auth.user };
}

const rootRoute = createRootRouteWithContext<RouterContext>()({
  // Silent refresh on app load: the session (if any) is known before any guard runs.
  beforeLoad: () => ensureSession(),
  component: RootLayout,
  pendingComponent: FullPageSpinner,
  notFoundComponent: NotFoundPage,
  errorComponent: ErrorPage,
});

const indexRoute = createRoute({
  getParentRoute: () => rootRoute,
  path: '/',
  beforeLoad: () => {
    throw redirect({ to: '/library' });
  },
});

// SCR-02 Sign in (public, no app shell).
const signInRoute = createRoute({
  getParentRoute: () => rootRoute,
  path: '/signin',
  validateSearch: (s: Record<string, unknown>): ReturnToSearch & { email?: string } => ({
    ...returnToSearch(s),
    email: str(s.email),
  }),
  beforeLoad: ({ search }) => redirectIfSignedIn(search.return_to),
  component: SignInPage,
});

// SCR-03 Sign up: /signup?invite=&plan=&name=&return_to=
const signUpRoute = createRoute({
  getParentRoute: () => rootRoute,
  path: '/signup',
  validateSearch: (
    s: Record<string, unknown>,
  ): ReturnToSearch & { invite?: string; plan?: string; name?: string } => ({
    ...returnToSearch(s),
    invite: str(s.invite),
    plan: s.plan === 'free' || s.plan === 'pro' || s.plan === 'team' ? s.plan : undefined,
    name: str(s.name),
  }),
  beforeLoad: ({ search }) =>
    redirectIfSignedIn(
      search.invite ? `/invite/${encodeURIComponent(search.invite)}` : search.return_to,
    ),
  component: SignUpPage,
});

// SCR-04 Forgot password (email prefilled from SCR-02).
const forgotRoute = createRoute({
  getParentRoute: () => rootRoute,
  path: '/forgot-password',
  validateSearch: (s: Record<string, unknown>): { email?: string } => ({ email: str(s.email) }),
  component: ForgotPasswordPage,
});

// SCR-05 Reset password (link from the email; works signed in or out).
const resetRoute = createRoute({
  getParentRoute: () => rootRoute,
  path: '/reset-password',
  validateSearch: (s: Record<string, unknown>): { token?: string } => ({ token: str(s.token) }),
  component: ResetPasswordPage,
});

// SCR-06 Verify email: ?token= (email link, public) or the inbox screen (signed in, unverified).
const verifyRoute = createRoute({
  getParentRoute: () => rootRoute,
  path: '/verify-email',
  validateSearch: (s: Record<string, unknown>): ReturnToSearch & { token?: string } => ({
    ...returnToSearch(s),
    token: str(s.token),
  }),
  beforeLoad: ({ search, location }) => {
    if (search.token) return;
    const auth = requireSignedIn(location.href);
    if (auth.user.email_verified) throw redirect({ href: nextAfterAuth(auth, search.return_to) });
  },
  component: VerifyEmailPage,
});

// Landing page after Google OIDC (identity → /auth/callback#…).
const authCallbackRoute = createRoute({
  getParentRoute: () => rootRoute,
  path: '/auth/callback',
  component: AuthCallbackPage,
});

// SCR-07 Onboarding wizard: /onboarding/organization → /region → /preferences.
const onboardingRoute = createRoute({
  getParentRoute: () => rootRoute,
  path: '/onboarding/$step',
  params: {
    parse: (p: { step: string }): { step: OnboardingStep } => {
      if (!(ONBOARDING_STEPS as readonly string[]).includes(p.step)) throw notFound();
      return { step: p.step as OnboardingStep };
    },
    stringify: (p: { step: OnboardingStep }) => ({ step: p.step }),
  },
  validateSearch: returnToSearch,
  beforeLoad: ({ params, search, location }) => {
    const auth = requireSignedIn(location.href);
    if (!auth.user.email_verified) {
      throw redirect({ to: '/verify-email', search: { return_to: search.return_to } });
    }
    const draft = useOnboardingDraft.getState();
    const hasOrg = auth.memberships.length > 0;
    if (params.step === 'preferences') {
      // Step 3 needs an org (created at step 2, or joined through an invitation).
      if (!hasOrg) {
        throw redirect({ to: '/onboarding/$step', params: { step: 'organization' }, search });
      }
      return;
    }
    // Already a member somewhere and not in the middle of this wizard → nothing to set up.
    if (hasOrg && !draft.createdOrgId) {
      throw redirect({ href: nextAfterAuth(auth, search.return_to) });
    }
    if (params.step === 'region' && !draft.name) {
      throw redirect({ to: '/onboarding/$step', params: { step: 'organization' }, search });
    }
  },
  component: OnboardingPage,
});

// SCR-52 Accept invitation (public; the page handles signed-in / signed-out states).
const inviteRoute = createRoute({
  getParentRoute: () => rootRoute,
  path: '/invite/$token',
  component: InvitePage,
});

// SCR-53 Legal (static, public).
const termsRoute = createRoute({
  getParentRoute: () => rootRoute,
  path: '/terms',
  component: () => <LegalPage doc="terms" />,
});
const privacyRoute = createRoute({
  getParentRoute: () => rootRoute,
  path: '/privacy',
  component: () => <LegalPage doc="privacy" />,
});

// Authenticated app layout with the global top bar (G). `?dialog=` hosts app-wide dialogs.
const appRoute = createRoute({
  getParentRoute: () => rootRoute,
  id: '_app',
  validateSearch: (s: Record<string, unknown>): { dialog?: AppDialog } => ({
    dialog: s.dialog === 'shortcuts' ? 'shortcuts' : undefined,
  }),
  beforeLoad: async ({ location }) => {
    const auth = requireSignedIn(location.href);
    const returnTo = safeReturnTo(location.href);
    if (!auth.user.email_verified) {
      throw redirect({ to: '/verify-email', search: { return_to: returnTo } });
    }
    const first = auth.memberships[0];
    if (!first) {
      throw redirect({
        to: '/onboarding/$step',
        params: { step: 'organization' },
        search: { return_to: returnTo },
      });
    }
    // Member somewhere but the token has no active org: activate the first membership.
    if (!auth.activeOrgId) await switchOrganization(first.org_id);
  },
  component: AppShell,
});

function oneOf<T extends string>(values: readonly T[], v: unknown): T | undefined {
  return typeof v === 'string' && (values as readonly string[]).includes(v) ? (v as T) : undefined;
}

export interface LibrarySearch {
  new?: NewAnalysisSource;
  q?: string;
  status?: StatusFilter;
  source?: SourceFilter;
  sort?: LibrarySort;
}

// SCR-10 Library (/library?q=&status=&source=&sort=); SCR-11 New analysis is a modal on top:
// /library?new=youtube|upload. Defaults (all / newest) are omitted from the URL.
const libraryRoute = createRoute({
  getParentRoute: () => appRoute,
  path: '/library',
  validateSearch: (s: Record<string, unknown>): LibrarySearch => {
    const status = oneOf(STATUS_FILTERS, s.status);
    const source = oneOf(SOURCE_FILTERS, s.source);
    const sort = oneOf(SORTS, s.sort);
    return {
      new: s.new === 'youtube' || s.new === 'upload' ? s.new : undefined,
      q: str(s.q),
      status: status === 'all' ? undefined : status,
      source: source === 'all' ? undefined : source,
      sort: sort === 'newest' ? undefined : sort,
    };
  },
  component: LibraryPage,
});

// Stable entry point for "new analysis" links (e.g. from marketing) → the modal on the library.
const newRoute = createRoute({
  getParentRoute: () => appRoute,
  path: '/new',
  beforeLoad: () => {
    throw redirect({ to: '/library', search: { new: 'youtube' } });
  },
});

const videoRoute = createRoute({
  getParentRoute: () => appRoute,
  path: '/videos/$videoId',
});

const videoIndexRoute = createRoute({
  getParentRoute: () => videoRoute,
  path: '/',
  beforeLoad: ({ params }) => {
    throw redirect({
      to: '/videos/$videoId/$tab',
      params: { videoId: params.videoId, tab: 'overview' },
    });
  },
});

// SCR-13 Processing.
const processingRoute = createRoute({
  getParentRoute: () => videoRoute,
  path: '/processing',
  component: ProcessingPage,
});

export type TeamsMode = 'confirm' | 'manual';

function seconds(v: unknown): number | undefined {
  const t = typeof v === 'number' ? v : Number(v);
  return v !== undefined && v !== '' && Number.isFinite(t) && t >= 0 ? t : undefined;
}

// SCR-14 Team confirmation: /videos/:id/teams?mode=confirm|manual&return_to=.
// Static segments outrank the `/$tab` param route, so `teams` / `calibrate` are never tabs.
const teamsRoute = createRoute({
  getParentRoute: () => videoRoute,
  path: '/teams',
  validateSearch: (s: Record<string, unknown>): ReturnToSearch & { mode?: TeamsMode } => ({
    ...returnToSearch(s),
    mode: s.mode === 'manual' ? 'manual' : undefined,
  }),
  component: TeamsPage,
});

// SCR-20 Analysis workspace: /videos/:id/:tab?t=<seconds>.
const workspaceRoute = createRoute({
  getParentRoute: () => videoRoute,
  path: '/$tab',
  params: {
    parse: (p: { tab: string }): { tab: WorkspaceTab } => {
      if (!(WORKSPACE_TABS as readonly string[]).includes(p.tab)) throw notFound();
      return { tab: p.tab as WorkspaceTab };
    },
    stringify: (p: { tab: WorkspaceTab }) => ({ tab: p.tab }),
  },
  validateSearch: (s: Record<string, unknown>): { t?: number } => ({ t: seconds(s.t) }),
  component: WorkspacePage,
});

// SCR-26 Exports.
const exportsRoute = createRoute({
  getParentRoute: () => appRoute,
  path: '/exports',
  component: ExportsPage,
});

// SCR-40 Settings.
const settingsIndexRoute = createRoute({
  getParentRoute: () => appRoute,
  path: '/settings',
  beforeLoad: () => {
    throw redirect({ to: '/settings/$section', params: { section: 'profile' } });
  },
});

const settingsRoute = createRoute({
  getParentRoute: () => appRoute,
  path: '/settings/$section',
  params: {
    parse: (p: { section: string }): { section: SettingsSection } => {
      if (!(SETTINGS_SECTIONS as readonly string[]).includes(p.section)) throw notFound();
      return { section: p.section as SettingsSection };
    },
    stringify: (p: { section: SettingsSection }) => ({ section: p.section }),
  },
  component: SettingsPage,
});

// Dev-only component gallery; the chunk is not part of production builds.
const devRoutes = import.meta.env.DEV
  ? [
      createRoute({
        getParentRoute: () => rootRoute,
        path: '/__ui',
        component: lazyRouteComponent(() => import('@/dev/UiGallery')),
      }),
      // Sync-engine bench: local video + synthetic manifest/tiles (M1-FE-08).
      createRoute({
        getParentRoute: () => rootRoute,
        path: '/__player',
        component: lazyRouteComponent(() => import('@/dev/PlayerDevPage')),
      }),
    ]
  : [];

export const routeTree = rootRoute.addChildren([
  indexRoute,
  signInRoute,
  signUpRoute,
  forgotRoute,
  resetRoute,
  verifyRoute,
  authCallbackRoute,
  onboardingRoute,
  inviteRoute,
  termsRoute,
  privacyRoute,
  appRoute.addChildren([
    libraryRoute,
    newRoute,
    videoRoute.addChildren([videoIndexRoute, processingRoute, teamsRoute, workspaceRoute]),
    exportsRoute,
    settingsIndexRoute,
    settingsRoute,
  ]),
  ...devRoutes,
]);

export function createAppRouter(opts: { queryClient: QueryClient; history?: RouterHistory }) {
  return createRouter({
    routeTree,
    history: opts.history,
    context: { queryClient: opts.queryClient },
    defaultPreload: 'intent',
    defaultPreloadStaleTime: 0, // TanStack Query owns caching
    defaultPendingMs: 300,
    scrollRestoration: true,
  });
}

declare module '@tanstack/react-router' {
  interface Register {
    router: ReturnType<typeof createAppRouter>;
  }
}
