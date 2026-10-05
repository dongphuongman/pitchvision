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
import { ErrorPage, NotFoundPage } from '@/app/StatusPages';
import { SETTINGS_SECTIONS, type SettingsSection } from '@/features/settings/sections';
import { WORKSPACE_TABS, type WorkspaceTab } from '@/features/video/tabs';

// Screens are code-split per route; the shell (top bar, dialogs host) stays in the entry chunk.
const LibraryPage = lazyRouteComponent(
  () => import('@/features/library/LibraryPage'),
  'LibraryPage',
);
const SignInPage = lazyRouteComponent(() => import('@/features/auth/SignInPage'), 'SignInPage');
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

/*
 * Code-based route tree (no codegen step). Route ids used with getRouteApi():
 *   /_app/library, /_app/videos/$videoId/$tab, /_app/settings/$section, /signin …
 * Phase C screens (Live) are intentionally absent.
 */

export interface RouterContext {
  queryClient: QueryClient;
}

export type AppDialog = 'shortcuts';
export type NewAnalysisSource = 'youtube' | 'upload';

const rootRoute = createRootRouteWithContext<RouterContext>()({
  component: RootLayout,
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
  validateSearch: (s: Record<string, unknown>): { return_to?: string } => ({
    return_to: typeof s.return_to === 'string' ? s.return_to : undefined,
  }),
  component: SignInPage,
});

// Authenticated app layout with the global top bar (G). `?dialog=` hosts app-wide dialogs.
const appRoute = createRoute({
  getParentRoute: () => rootRoute,
  id: '_app',
  validateSearch: (s: Record<string, unknown>): { dialog?: AppDialog } => ({
    dialog: s.dialog === 'shortcuts' ? 'shortcuts' : undefined,
  }),
  component: AppShell,
});

// SCR-10 Library; SCR-11 New analysis is a modal on top: /library?new=youtube|upload.
const libraryRoute = createRoute({
  getParentRoute: () => appRoute,
  path: '/library',
  validateSearch: (s: Record<string, unknown>): { new?: NewAnalysisSource } => ({
    new: s.new === 'youtube' || s.new === 'upload' ? s.new : undefined,
  }),
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
  validateSearch: (s: Record<string, unknown>): { t?: number } => {
    const t = typeof s.t === 'number' ? s.t : Number(s.t);
    return { t: Number.isFinite(t) && t >= 0 ? t : undefined };
  },
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
    ]
  : [];

export const routeTree = rootRoute.addChildren([
  indexRoute,
  signInRoute,
  appRoute.addChildren([
    libraryRoute,
    newRoute,
    videoRoute.addChildren([videoIndexRoute, processingRoute, workspaceRoute]),
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
    scrollRestoration: true,
  });
}

declare module '@tanstack/react-router' {
  interface Register {
    router: ReturnType<typeof createAppRouter>;
  }
}
