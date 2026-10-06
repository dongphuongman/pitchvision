import type { QueryClient } from '@tanstack/react-query';
import { create } from 'zustand';
import { deleteVideo, removeVideoFromCache } from '@/api/videos';

/**
 * DLG-01 "Delete … can't be undone after 10 seconds" (UIUX §DLG): the video disappears at once
 * (hidden via this store, so a list refetch during the window can't resurrect it), the DELETE
 * is sent when the 10 s Undo window ends. Timers live outside React so leaving the page still
 * deletes; closing the tab inside the window keeps the video (fail-safe).
 */
export const UNDO_WINDOW_MS = 10_000;

interface PendingDeletesState {
  ids: ReadonlySet<string>;
  add: (id: string) => void;
  remove: (id: string) => void;
}

export const usePendingDeletes = create<PendingDeletesState>()((set) => ({
  ids: new Set(),
  add: (id) => set((s) => ({ ids: new Set(s.ids).add(id) })),
  remove: (id) =>
    set((s) => {
      const ids = new Set(s.ids);
      ids.delete(id);
      return { ids };
    }),
}));

const timers = new Map<string, ReturnType<typeof setTimeout>>();

export interface ScheduledDelete {
  undo: () => void;
}

export function scheduleDelete(
  qc: QueryClient,
  videoId: string,
  callbacks: { onError?: (e: unknown) => void; onDeleted?: () => void } = {},
  delayMs = UNDO_WINDOW_MS,
): ScheduledDelete {
  const store = usePendingDeletes.getState();
  store.add(videoId);
  const prev = timers.get(videoId);
  if (prev) clearTimeout(prev);
  timers.set(
    videoId,
    setTimeout(() => {
      timers.delete(videoId);
      deleteVideo(videoId).then(
        () => {
          removeVideoFromCache(qc, videoId);
          usePendingDeletes.getState().remove(videoId);
          callbacks.onDeleted?.();
        },
        (e: unknown) => {
          usePendingDeletes.getState().remove(videoId);
          callbacks.onError?.(e);
        },
      );
    }, delayMs),
  );
  return {
    undo: () => {
      const t = timers.get(videoId);
      if (!t) return; // already sent
      clearTimeout(t);
      timers.delete(videoId);
      usePendingDeletes.getState().remove(videoId);
    },
  };
}

/** Tests: cancel every pending deletion. */
export function resetPendingDeletesForTests(): void {
  for (const t of timers.values()) clearTimeout(t);
  timers.clear();
  usePendingDeletes.setState({ ids: new Set() });
}
