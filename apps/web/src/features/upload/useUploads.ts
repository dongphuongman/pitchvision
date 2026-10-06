import { useQueryClient } from '@tanstack/react-query';
import { useEffect, useSyncExternalStore } from 'react';
import {
  abortUpload,
  completeUpload,
  createJob,
  createUpload,
  getUploadState,
  signParts,
} from '@/api/uploads';
import { videoKeys } from '@/api/videos';
import { useSignOutGuard } from '@/auth/useAuthActions';
import { UploadManager, xhrPutPart, type UploadItem } from './uploadManager';

/** One manager per tab: uploads keep running when the modal closes (E15 "Continue in background"). */
export const uploads = new UploadManager({
  api: { createUpload, signParts, getUploadState, completeUpload, abortUpload, createJob },
  putPart: xhrPutPart,
});

let snapshot: UploadItem[] = uploads.list();
uploads.subscribe((items) => {
  snapshot = items;
  useSignOutGuard.getState().setUploadsInProgress(uploads.activeCount());
});

export function resetUploadsForTests(): void {
  uploads.reset();
}

export function useUploads(): UploadItem[] {
  return useSyncExternalStore(
    (cb) => uploads.subscribe(cb),
    () => snapshot,
    () => snapshot,
  );
}

export function useUpload(id: string | null): UploadItem | undefined {
  const items = useUploads();
  return id ? items.find((i) => i.id === id) : undefined;
}

/**
 * Mounted once in the app shell: refresh the library when an upload finishes and warn before the tab
 * closes while bytes are still moving (the multipart upload would have to be resumed by re-selecting
 * the file).
 */
export function useUploadLifecycle(): void {
  const qc = useQueryClient();
  useEffect(() => {
    const seen = new Set<string>();
    const off = uploads.subscribe((items) => {
      for (const it of items) {
        const key = `${it.id}:${it.phase}`;
        if (seen.has(key)) continue;
        seen.add(key);
        if (it.phase === 'uploading' || it.phase === 'done' || it.phase === 'cancelled') {
          void qc.invalidateQueries({ queryKey: videoKeys.all });
        }
      }
    });
    const onBeforeUnload = (e: BeforeUnloadEvent) => {
      if (uploads.activeCount() > 0) e.preventDefault();
    };
    window.addEventListener('beforeunload', onBeforeUnload);
    return () => {
      off();
      window.removeEventListener('beforeunload', onBeforeUnload);
    };
  }, [qc]);
}
