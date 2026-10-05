import { lazy, Suspense } from 'react';
import { useResolvedTheme, useSystemThemeListener, useThemeStore } from '@/lib/theme';
import { Button, Toaster, TooltipProvider } from '@/ui';

// Dev-only: the gallery chunk is dropped from production builds.
const UiGallery = import.meta.env.DEV ? lazy(() => import('@/dev/UiGallery')) : null;

/** Temporary root (M1-FE-01/02). Replaced by the router + app shell in M1-FE-03. */
export function App() {
  useSystemThemeListener();
  const theme = useResolvedTheme();
  const toggle = useThemeStore((s) => s.toggle);
  const showGallery = UiGallery !== null && window.location.pathname === '/__ui';

  return (
    <TooltipProvider delayDuration={400}>
      {showGallery ? (
        <Suspense>
          <UiGallery />
        </Suspense>
      ) : (
        <main className="mx-auto flex max-w-[1400px] items-center gap-3.5 px-8 pt-[26px]">
          <h1>PitchVision</h1>
          <div className="flex-1" />
          <Button onClick={toggle}>{theme}</Button>
        </main>
      )}
      <Toaster />
    </TooltipProvider>
  );
}
