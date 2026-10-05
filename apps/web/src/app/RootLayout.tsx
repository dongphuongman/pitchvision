import { Outlet } from '@tanstack/react-router';
import { useSystemThemeListener } from '@/lib/theme';
import { Toaster, TooltipProvider } from '@/ui';

export function RootLayout() {
  useSystemThemeListener();
  return (
    <TooltipProvider delayDuration={400}>
      <Outlet />
      <Toaster />
    </TooltipProvider>
  );
}
