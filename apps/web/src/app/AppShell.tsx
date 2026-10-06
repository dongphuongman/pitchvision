import { Outlet, useNavigate, useRouterState } from '@tanstack/react-router';
import { useEffect } from 'react';
import { can } from '@/auth/roles';
import { useRole } from '@/auth/session';
import type { AppDialog, UpgradeReason } from '@/router';
import { UploadTray } from '@/features/upload/UploadTray';
import { useUploadLifecycle } from '@/features/upload/useUploads';
import { useSearchDialog } from '@/lib/useSearchDialog';
import { ShortcutsDialog } from './ShortcutsDialog';
import { UpgradeDialog } from './UpgradeDialog';
import { TopBar } from './TopBar';

function isTypingTarget(el: EventTarget | null): boolean {
  if (!(el instanceof HTMLElement)) return false;
  return el.isContentEditable || ['INPUT', 'TEXTAREA', 'SELECT'].includes(el.tagName);
}

/** Authenticated layout: top bar, page outlet, app-wide `?dialog=` dialogs, global shortcuts. */
export function AppShell() {
  const navigate = useNavigate();
  const dialog = useSearchDialog<AppDialog>('dialog');
  const reason = useRouterState({
    select: (st) => (st.location.search as { reason?: UpgradeReason }).reason,
  });
  // N = New analysis (SCR-11) is Editor+ only, like the top-bar button.
  const canCreate = can(useRole(), 'editor');
  useUploadLifecycle();

  useEffect(() => {
    function onKey(e: KeyboardEvent) {
      if (e.defaultPrevented || e.metaKey || e.ctrlKey || e.altKey) return;
      if (isTypingTarget(e.target) || document.querySelector('[role="dialog"]')) return;
      if (e.key === '?') {
        e.preventDefault();
        void dialog.open('shortcuts');
      } else if ((e.key === 'n' || e.key === 'N') && canCreate) {
        e.preventDefault();
        void navigate({
          to: '/library',
          search: { new: 'youtube' },
          state: { pvDialog: 'new' } as never,
        });
      }
    }
    window.addEventListener('keydown', onKey);
    return () => window.removeEventListener('keydown', onKey);
  }, [dialog, navigate, canCreate]);

  return (
    <div className="flex min-h-screen flex-col">
      <TopBar onOpenShortcuts={() => void dialog.open('shortcuts')} />
      <main id="main" className="flex-1">
        <Outlet />
      </main>
      <ShortcutsDialog open={dialog.value === 'shortcuts'} onOpenChange={dialog.onOpenChange} />
      <UpgradeDialog
        open={dialog.value === 'upgrade'}
        reason={reason}
        onOpenChange={dialog.onOpenChange}
      />
      <UploadTray />
    </div>
  );
}
