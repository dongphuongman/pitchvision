import { useMutation, useQueryClient } from '@tanstack/react-query';
import { useEffect, useRef, useState } from 'react';
import { useTranslation } from 'react-i18next';
import { applyCorrections, patchPlayers, type CorrectionIn } from '@/api/corrections';
import type { TeamInfo } from '@/api/types';
import { Badge, Button, toast } from '@/ui';
import { usePostCorrection } from './trackEdits';

export interface TrackMenuAnchor {
  trackId: number;
  x: number;
  y: number;
  source: 'video' | 'map';
}

interface MenuProps {
  videoId: string;
  anchor: TrackMenuAnchor;
  teamLabel: string;
  teams: readonly TeamInfo[];
  /** Snapped video time of the current frame (split point / single-frame remove). */
  frameMs: number;
  frameLenMs: number;
  /** Merge-pick mode: the next click is the merge target. */
  mergeFrom: number | null;
  onEnterMerge: (from: number) => void;
  onOpenHeatmap: (trackId: number) => void;
  onClose: () => void;
}

/** SCR-22 correction context menu (M2-FE-03): merge / split / remove + change team. */
export function TrackCorrectionMenu({
  videoId,
  anchor,
  teamLabel,
  teams,
  frameMs,
  frameLenMs,
  mergeFrom,
  onEnterMerge,
  onOpenHeatmap,
  onClose,
}: MenuProps) {
  const { t } = useTranslation('video');
  const qc = useQueryClient();
  const [confirm, setConfirm] = useState<'split' | 'whole' | null>(null);
  const [range, setRange] = useState(false);
  const [fromS, setFromS] = useState('');
  const [toS, setToS] = useState('');
  const ref = useRef<HTMLDivElement>(null);

  useEffect(() => {
    ref.current?.querySelector<HTMLButtonElement>('button')?.focus();
  }, []);
  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      if (e.key === 'Escape') onClose();
    };
    window.addEventListener('keydown', onKey);
    return () => window.removeEventListener('keydown', onKey);
  }, [onClose]);

  const post = usePostCorrection(videoId, {
    doneLabel: t('trackEdits.done'),
    undoLabel: t('trackEdits.undo'),
    onDone: onClose,
  });
  const teamFix = useMutation({
    mutationFn: (args: { team: number | null; referee: boolean }) =>
      patchPlayers(
        videoId,
        args.referee
          ? [{ track_id: anchor.trackId, role: 'referee' }]
          : [{ track_id: anchor.trackId, role: 'player', team: args.team }],
      ),
    onSuccess: () => {
      void qc.invalidateQueries({ queryKey: ['results', videoId] });
      toast({ title: t('trackEdits.teamFixed'), tone: 'success' });
      onClose();
    },
  });

  const err = post.isError || teamFix.isError;
  const w = 264;
  const x = Math.max(8, Math.min(anchor.x, window.innerWidth - w - 8));
  const y = Math.max(8, Math.min(anchor.y, window.innerHeight - 320));

  const item =
    'flex w-full items-center gap-2 rounded-md px-2.5 py-2 text-left text-body-sm hover:bg-surface-3 focus-ring disabled:opacity-50';
  const busy = post.isPending || teamFix.isPending;

  // Merge-pick target confirmation: "Merge #from into #target?"
  if (mergeFrom !== null && mergeFrom !== anchor.trackId) {
    return (
      <div
        ref={ref}
        role="menu"
        aria-label={t('trackEdits.mergeTitle')}
        className="fixed z-[90] rounded-xl border border-line bg-surface-2 p-1.5 shadow-pop"
        style={{ left: x, top: y, width: w }}
      >
        <p className="text-body-sm m-0 px-2.5 py-2">
          {t('trackEdits.mergeConfirm', { from: mergeFrom, into: anchor.trackId })}
        </p>
        <button
          type="button"
          role="menuitem"
          className={item}
          disabled={busy}
          onClick={() => post.mutate({ type: 'merge', from: mergeFrom, into: anchor.trackId })}
        >
          {t('trackEdits.merge')}
        </button>
        <button type="button" role="menuitem" className={item} onClick={onClose}>
          {t('trackEdits.cancel')}
        </button>
        {err && (
          <p className="m-0 px-2.5 py-1 text-caption text-danger">{t('trackEdits.failed')}</p>
        )}
      </div>
    );
  }

  const submitRange = () => {
    const from = Math.round(Number(fromS) * 1000);
    const to = Math.round(Number(toS) * 1000);
    if (!Number.isFinite(from) || !Number.isFinite(to) || to <= from) return;
    const body: CorrectionIn = {
      type: 'remove',
      track: anchor.trackId,
      from_ms: from,
      to_ms: to,
    };
    post.mutate(body);
  };

  return (
    <div
      ref={ref}
      role="menu"
      aria-label={t('trackEdits.title')}
      className="fixed z-[90] max-h-[70vh] overflow-auto rounded-xl border border-line bg-surface-2 p-1.5 shadow-pop"
      style={{ left: x, top: y, width: w }}
      onClick={(e) => e.stopPropagation()}
      onContextMenu={(e) => e.preventDefault()}
    >
      <p className="text-body-sm m-0 truncate px-2.5 py-2 font-semibold">
        {t('trackEdits.header', { id: anchor.trackId, team: teamLabel })}
      </p>

      <p className="m-0 px-2.5 pt-1 text-caption font-semibold text-fg-3 uppercase">
        {t('trackEdits.changeTeam')}
      </p>
      {teams.map((team) => (
        <button
          key={team.cluster}
          type="button"
          role="menuitem"
          className={item}
          disabled={busy}
          onClick={() => teamFix.mutate({ team: team.cluster, referee: false })}
        >
          <span
            aria-hidden="true"
            className="h-3 w-3 rounded-full"
            style={{ background: team.color }}
          />
          {team.name}
        </button>
      ))}
      <button
        type="button"
        role="menuitem"
        className={item}
        disabled={busy}
        onClick={() => teamFix.mutate({ team: null, referee: true })}
      >
        {t('trackEdits.referee')}
      </button>

      <p className="m-0 px-2.5 pt-2 text-caption font-semibold text-fg-3 uppercase">
        {t('trackEdits.correct')}
      </p>
      <button
        type="button"
        role="menuitem"
        className={item}
        disabled={busy}
        onClick={() => onEnterMerge(anchor.trackId)}
      >
        {t('trackEdits.mergeWith')}
      </button>
      {confirm === 'split' ? (
        <div className="px-2.5 py-1.5">
          <p className="text-body-sm m-0 mb-1.5">{t('trackEdits.splitConfirm')}</p>
          <div className="flex gap-2">
            <Button
              size="sm"
              variant="primary"
              loading={post.isPending}
              onClick={() => post.mutate({ type: 'split', track: anchor.trackId, at_ms: frameMs })}
            >
              {t('trackEdits.split')}
            </Button>
            <Button size="sm" variant="ghost" onClick={() => setConfirm(null)}>
              {t('trackEdits.cancel')}
            </Button>
          </div>
        </div>
      ) : (
        <button
          type="button"
          role="menuitem"
          className={item}
          disabled={busy}
          onClick={() => setConfirm('split')}
        >
          {t('trackEdits.splitAt')}
        </button>
      )}
      <button
        type="button"
        role="menuitem"
        className={item}
        disabled={busy}
        onClick={() =>
          post.mutate({
            type: 'remove',
            track: anchor.trackId,
            from_ms: frameMs,
            to_ms: frameMs + frameLenMs,
          })
        }
      >
        {t('trackEdits.removeFrame')}
      </button>
      {confirm === 'whole' ? (
        <div className="px-2.5 py-1.5">
          <p className="text-body-sm m-0 mb-1.5">{t('trackEdits.removeConfirm')}</p>
          <div className="flex gap-2">
            <Button
              size="sm"
              variant="primary"
              loading={post.isPending}
              onClick={() => post.mutate({ type: 'remove', track: anchor.trackId })}
            >
              {t('trackEdits.remove')}
            </Button>
            <Button size="sm" variant="ghost" onClick={() => setConfirm(null)}>
              {t('trackEdits.cancel')}
            </Button>
          </div>
        </div>
      ) : (
        <button
          type="button"
          role="menuitem"
          className={item}
          disabled={busy}
          onClick={() => setConfirm('whole')}
        >
          {t('trackEdits.removeWhole')}
        </button>
      )}
      {range ? (
        <div className="px-2.5 py-1.5">
          <p className="text-body-sm m-0 mb-1.5">{t('trackEdits.rangeHint')}</p>
          <div className="mb-1.5 flex items-center gap-1.5">
            <input
              aria-label={t('trackEdits.from')}
              className="bg-surface text-body-sm w-full rounded-md border border-line px-2 py-1.5"
              inputMode="decimal"
              placeholder="12.5"
              value={fromS}
              onChange={(e) => setFromS(e.target.value)}
            />
            <span aria-hidden="true">–</span>
            <input
              aria-label={t('trackEdits.to')}
              className="bg-surface text-body-sm w-full rounded-md border border-line px-2 py-1.5"
              inputMode="decimal"
              placeholder="18.0"
              value={toS}
              onChange={(e) => setToS(e.target.value)}
            />
          </div>
          <div className="flex gap-2">
            <Button
              size="sm"
              variant="primary"
              loading={post.isPending}
              disabled={fromS.trim() === '' || toS.trim() === ''}
              onClick={submitRange}
            >
              {t('trackEdits.remove')}
            </Button>
            <Button size="sm" variant="ghost" onClick={() => setRange(false)}>
              {t('trackEdits.cancel')}
            </Button>
          </div>
        </div>
      ) : (
        <button
          type="button"
          role="menuitem"
          className={item}
          disabled={busy}
          onClick={() => setRange(true)}
        >
          {t('trackEdits.removeRange')}
        </button>
      )}
      <button
        type="button"
        role="menuitem"
        className={item}
        onClick={() => {
          onOpenHeatmap(anchor.trackId);
          onClose();
        }}
      >
        {t('trackEdits.openHeatmap')}
      </button>
      {err && <p className="m-0 px-2.5 py-1 text-caption text-danger">{t('trackEdits.failed')}</p>}
    </div>
  );
}

/** "Stats updating…" badge (SCR-22: results follow with the edit job) + Apply when blocked. */
export function CorrectionsBadge({
  videoId,
  state,
}: {
  videoId: string;
  state: { pending: boolean; job?: { id: string } | null } | undefined;
}) {
  const { t } = useTranslation('video');
  const qc = useQueryClient();
  const [failed, setFailed] = useState(false);
  if (!state || !state.pending) return null;
  return (
    <span className="inline-flex items-center gap-2">
      <Badge tone="warn" pulse>
        {t('trackEdits.updating')}
      </Badge>
      {state.job == null && (
        <Button
          size="sm"
          variant="ghost"
          onClick={() => {
            setFailed(false);
            void (async () => {
              try {
                await applyCorrections(videoId);
                await qc.invalidateQueries({
                  queryKey: ['results', videoId, 'corrections'],
                });
              } catch {
                setFailed(true);
              }
            })();
          }}
        >
          {t('trackEdits.apply')}
        </Button>
      )}
      {failed && <span className="text-caption text-danger">{t('trackEdits.failed')}</span>}
    </span>
  );
}
