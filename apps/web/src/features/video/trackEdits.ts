import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { useEffect, useRef } from 'react';
import {
  applyCorrections,
  correctionsQuery,
  postCorrection,
  undoCorrection,
  type CorrectionIn,
  type CorrectionsState,
} from '@/api/corrections';
import type { TileFrame } from '@/api/types';
import { OBJ, OBJ_CLS } from '@/player/tileCodec';
import { toast } from '@/ui';

/* ------------------------------------------------------------------ pure hit-test */

/** Source-pixel point → smallest containing box's track (ball ignored). */
export function pickVideoTrack(objs: TileFrame['objs'], px: number, py: number): number | null {
  if (!objs) return null;
  let best: number | null = null;
  let bestArea = Infinity;
  for (const o of objs) {
    if (o[OBJ.cls] === OBJ_CLS.ball) continue;
    const x1 = o[OBJ.x1];
    const y1 = o[OBJ.y1];
    const x2 = o[OBJ.x2];
    const y2 = o[OBJ.y2];
    if (px < x1 || px > x2 || py < y1 || py > y2) continue;
    const area = Math.max(0, x2 - x1) * Math.max(0, y2 - y1);
    if (area < bestArea) {
      bestArea = area;
      best = o[OBJ.trackId];
    }
  }
  return best;
}

export interface MapDotLike {
  trackId: number;
  x: number;
  y: number;
}

/** Pitch-metre point → nearest dot within `tolM` (map clicks are imprecise). */
export function pickMapTrack(
  dots: readonly MapDotLike[],
  mx: number,
  my: number,
  tolM = 2,
): number | null {
  let best: number | null = null;
  let bestD = tolM;
  for (const d of dots) {
    const dist = Math.hypot(d.x - mx, d.y - my);
    if (dist < bestD) {
      bestD = dist;
      best = d.trackId;
    }
  }
  return best;
}

/* ------------------------------------------------------- corrections state hook */

export function useCorrectionsState(videoId: string, enabled = true) {
  const qc = useQueryClient();
  const q = useQuery({ ...correctionsQuery(videoId), enabled });
  const pending = q.data?.pending ?? false;

  // The edit job rewrites tiles/positions/stats under a new revision: refetch results
  // (manifest, stats, heatmap, passes) once it lands.
  const wasPending = useRef(false);
  useEffect(() => {
    if (wasPending.current && !pending) {
      void qc.invalidateQueries({ queryKey: ['results', videoId] });
    }
    wasPending.current = pending;
  }, [pending, qc, videoId]);

  const apply = useMutation({
    mutationFn: () => applyCorrections(videoId),
    onSuccess: () => qc.invalidateQueries({ queryKey: ['results', videoId, 'corrections'] }),
  });

  return { query: q, state: q.data, pending, apply };
}

/** POST a correction, then toast it with a 10 s Undo (UIUX SCR-22). */
export function usePostCorrection(
  videoId: string,
  opts: { doneLabel: string; undoLabel: string; onDone: () => void },
) {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (body: CorrectionIn) => postCorrection(videoId, body),
    onSuccess: (state: CorrectionsState) => {
      void qc.invalidateQueries({ queryKey: ['results', videoId, 'corrections'] });
      const last = state.items[state.items.length - 1];
      if (last) {
        toast({
          title: opts.doneLabel,
          tone: 'success',
          action: {
            label: opts.undoLabel,
            onClick: () => {
              void undoCorrection(videoId, last.id)
                .then(() => {
                  void qc.invalidateQueries({
                    queryKey: ['results', videoId, 'corrections'],
                  });
                })
                .catch(() => undefined);
            },
          },
        });
      }
      opts.onDone();
    },
  });
}
