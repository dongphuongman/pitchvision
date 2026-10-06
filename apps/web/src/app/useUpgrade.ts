import { useNavigate } from '@tanstack/react-router';
import { useCallback } from 'react';
import type { UpgradeReason } from '@/router';

/**
 * Opens SCR-41 over the current page (`?dialog=upgrade&reason=`): every plan block (F10) calls
 * this. Pushes a history entry so Back closes it (same marker as useSearchDialog).
 */
export function useOpenUpgrade() {
  const navigate = useNavigate();
  return useCallback(
    /** `close`: search params of the dialog that blocked (e.g. `export`) — replaced by this one. */
    (reason?: UpgradeReason, close: readonly string[] = []) =>
      void navigate({
        to: '.',
        search: ((prev: Record<string, unknown>) => ({
          ...Object.fromEntries(Object.entries(prev).filter(([k]) => !close.includes(k))),
          dialog: 'upgrade',
          reason,
        })) as never,
        state: ((prev: Record<string, unknown>) => ({ ...prev, pvDialog: 'dialog' })) as never,
      }),
    [navigate],
  );
}
