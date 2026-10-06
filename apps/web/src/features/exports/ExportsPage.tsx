import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { Link } from '@tanstack/react-router';
import { Download, MoreHorizontal, RotateCcw, X } from 'lucide-react';
import { useState } from 'react';
import { useTranslation } from 'react-i18next';
import {
  cancelExport,
  deleteExport,
  exportDownloadUrl,
  exportKeys,
  exportsQuery,
  retryExport,
  type ExportOut,
  type ExportStatusFilter,
} from '@/api/exports';
import { Page, PageHeader } from '@/app/Page';
import { formatBytes } from '@/i18n/format';
import {
  Badge,
  Button,
  Dialog,
  DialogClose,
  DropdownMenu,
  DropdownMenuContent,
  DropdownMenuItem,
  DropdownMenuTrigger,
  EmptyState,
  IconButton,
  Progress,
  SegmentedControl,
  Skeleton,
  toast,
} from '@/ui';
import type { BadgeTone } from '@/ui/classes';
import { daysLeft } from './model';

function statusTone(s: ExportOut['status']): BadgeTone {
  if (s === 'done') return 'ok';
  if (s === 'failed') return 'err';
  if (s === 'queued' || s === 'running') return 'run';
  return 'mute';
}

/**
 * SCR-26 Exports: status filter (E01), source link (E02), download via a fresh presigned URL (E03),
 * ⋯ copy link / delete (E04), cancel (E05), retry (E06), empty state (E07). Polls while an export
 * is running.
 */
export function ExportsPage() {
  const { t, i18n } = useTranslation('video');
  const [status, setStatus] = useState<ExportStatusFilter>('all');
  const [toDelete, setToDelete] = useState<ExportOut | null>(null);
  const q = useQuery(exportsQuery(status));
  const qc = useQueryClient();
  const refresh = () => void qc.invalidateQueries({ queryKey: exportKeys.all });

  const cancel = useMutation({ mutationFn: cancelExport, onSettled: refresh });
  const retry = useMutation({ mutationFn: retryExport, onSettled: refresh });
  const remove = useMutation({
    mutationFn: deleteExport,
    onSuccess: () => toast({ title: t('exports.deleted'), tone: 'success' }),
    onSettled: refresh,
  });

  async function download(e: ExportOut) {
    try {
      window.location.assign(await exportDownloadUrl(e.id));
    } catch {
      toast({ title: t('exports.downloadFailed'), tone: 'error' });
      refresh();
    }
  }

  const items = q.data?.items ?? [];
  return (
    <Page>
      <PageHeader
        title={t('exports.title')}
        actions={
          <SegmentedControl<ExportStatusFilter>
            value={status}
            onValueChange={setStatus}
            options={(['all', 'active', 'done'] as const).map((v) => ({
              value: v,
              label: t(`exports.filter.${v}`),
            }))}
          />
        }
      />
      {q.isPending ? (
        <div className="flex flex-col gap-2">
          <Skeleton className="h-12" />
          <Skeleton className="h-12" />
        </div>
      ) : items.length === 0 ? (
        <EmptyState
          icon={<Download size={30} />}
          title={t('exports.empty')}
          variant="plain"
          actions={
            <Button asChild>
              <Link to="/library">{t('exports.goToLibrary')}</Link>
            </Button>
          }
        />
      ) : (
        <table className="w-full border-collapse text-control">
          <thead>
            <tr className="border-b border-line text-left text-xs text-fg-3">
              <th className="py-2 pr-3 font-semibold">{t('exports.col.file')}</th>
              <th className="py-2 pr-3 font-semibold">{t('exports.col.source')}</th>
              <th className="py-2 pr-3 font-semibold">{t('exports.col.status')}</th>
              <th className="py-2 pr-3 font-semibold">{t('exports.col.expires')}</th>
              <th className="py-2 font-semibold">
                <span className="sr-only">{t('exports.col.actions')}</span>
              </th>
            </tr>
          </thead>
          <tbody>
            {items.map((e) => {
              const left = e.status === 'done' ? daysLeft(e.expires_at) : null;
              return (
                <tr key={e.id} className="border-b border-line">
                  <td className="py-2.5 pr-3">
                    <div className="font-semibold text-fg">{e.filename}</div>
                    {e.size_bytes ? (
                      <div className="text-xs text-fg-3">
                        {formatBytes(i18n.language, e.size_bytes)}
                      </div>
                    ) : null}
                  </td>
                  <td className="py-2.5 pr-3">
                    <Link
                      to="/videos/$videoId/$tab"
                      params={{ videoId: e.video_id, tab: 'overview' }}
                      className="rounded-xs text-fg-2 focus-ring hover:text-fg hover:underline"
                    >
                      {e.video_title ?? e.video_id}
                    </Link>
                  </td>
                  <td className="py-2.5 pr-3">
                    {e.status === 'running' ? (
                      <div className="flex w-40 items-center gap-2">
                        <Progress value={e.progress} label={e.filename} size="sm" />
                        <span className="text-xs text-fg-2 tabular-nums">
                          {t('exports.status.running', { pct: Math.round(e.progress) })}
                        </span>
                      </div>
                    ) : (
                      <Badge tone={statusTone(e.status)}>{t(`exports.status.${e.status}`)}</Badge>
                    )}
                  </td>
                  <td className="py-2.5 pr-3 text-fg-2">
                    {left === null
                      ? '—'
                      : left === 0
                        ? t('exports.expiresToday')
                        : t('exports.expiresIn', { count: left })}
                  </td>
                  <td className="py-2.5">
                    <div className="flex items-center justify-end gap-1">
                      {e.status === 'done' && (
                        <IconButton
                          label={t('exports.download')}
                          icon={<Download size={16} />}
                          onClick={() => void download(e)}
                        />
                      )}
                      {(e.status === 'queued' || e.status === 'running') && (
                        <Button
                          size="sm"
                          variant="ghost"
                          onClick={() => cancel.mutate(e.id)}
                          loading={cancel.isPending && cancel.variables === e.id}
                        >
                          <X size={14} aria-hidden="true" />
                          {t('exports.cancel')}
                        </Button>
                      )}
                      {(e.status === 'failed' || e.status === 'cancelled') && (
                        <Button
                          size="sm"
                          variant="ghost"
                          onClick={() => retry.mutate(e.id)}
                          loading={retry.isPending && retry.variables === e.id}
                        >
                          <RotateCcw size={14} aria-hidden="true" />
                          {t('exports.retry')}
                        </Button>
                      )}
                      <DropdownMenu modal={false}>
                        <DropdownMenuTrigger asChild>
                          <IconButton
                            label={t('exports.more', { file: e.filename })}
                            tooltip={false}
                            icon={<MoreHorizontal size={16} />}
                          />
                        </DropdownMenuTrigger>
                        <DropdownMenuContent align="end">
                          <DropdownMenuItem
                            onSelect={() => {
                              void navigator.clipboard
                                .writeText(`${window.location.origin}/exports`)
                                .then(() => toast({ title: t('exports.linkCopied') }));
                            }}
                          >
                            {t('exports.copyLink')}
                          </DropdownMenuItem>
                          <DropdownMenuItem onSelect={() => setToDelete(e)}>
                            {t('exports.delete')}
                          </DropdownMenuItem>
                        </DropdownMenuContent>
                      </DropdownMenu>
                    </div>
                  </td>
                </tr>
              );
            })}
          </tbody>
        </table>
      )}
      <Dialog
        open={toDelete !== null}
        onOpenChange={(open) => !open && setToDelete(null)}
        size="sm"
        title={t('exports.delete')}
        footer={
          <>
            <DialogClose asChild>
              <Button variant="ghost">{t('exports.cancel')}</Button>
            </DialogClose>
            <Button
              variant="danger"
              onClick={() => {
                if (toDelete) remove.mutate(toDelete.id);
                setToDelete(null);
              }}
            >
              {t('exports.delete')}
            </Button>
          </>
        }
      >
        <p className="m-0 text-fg-2">
          {t('exports.deleteConfirm', { file: toDelete?.filename ?? '' })}
        </p>
      </Dialog>
    </Page>
  );
}
