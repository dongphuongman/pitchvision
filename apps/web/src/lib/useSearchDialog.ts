import { useNavigate, useRouter, useRouterState } from '@tanstack/react-router';
import { useCallback } from 'react';

/** History-state marker: this entry was pushed by opening a dialog in-app. */
interface DialogHistoryState {
  pvDialog?: string;
}

/**
 * Modal ↔ query-string binding (UIUX §3.1: "modal qua query string").
 *
 * - The dialog is open while `?<param>=<value>` is present, so links like
 *   `/library?new=upload` open it directly and can be shared.
 * - Opening pushes a history entry → the browser Back button closes the dialog.
 * - Closing pops that entry when we pushed it (keeps history clean); for a deep link it
 *   replaces the URL without the param instead of leaving the app.
 */
export function useSearchDialog<V extends string>(param: string) {
  const navigate = useNavigate();
  const router = useRouter();
  const value = useRouterState({
    select: (s) => {
      const v = (s.location.search as Record<string, unknown>)[param];
      return typeof v === 'string' ? (v as V) : undefined;
    },
  });
  const pushedByUs = useRouterState({
    select: (s) => (s.location.state as DialogHistoryState).pvDialog === param,
  });

  const setParam = useCallback(
    (next: V | undefined, replace: boolean) =>
      navigate({
        to: '.',
        search: ((prev: Record<string, unknown>) => ({ ...prev, [param]: next })) as never,
        // Mark only entries we push; a replace keeps the current entry's origin (deep link or not).
        state: ((prev: DialogHistoryState) => ({
          ...prev,
          pvDialog: next === undefined ? undefined : replace ? prev.pvDialog : param,
        })) as never,
        replace,
      }),
    [navigate, param],
  );

  /** Open (push) or switch value within the open dialog (replace, e.g. tabs). */
  const open = useCallback((next: V) => setParam(next, value !== undefined), [setParam, value]);

  const close = useCallback(() => {
    if (value === undefined) return;
    if (pushedByUs) router.history.back();
    else void setParam(undefined, true);
  }, [pushedByUs, router, setParam, value]);

  return {
    value,
    isOpen: value !== undefined,
    open,
    close,
    onOpenChange: (o: boolean) => {
      if (!o) close();
    },
  };
}
