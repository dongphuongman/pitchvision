import { useNavigate } from '@tanstack/react-router';
import { CheckCircle2, Upload, X, XCircle } from 'lucide-react';
import { useTranslation } from 'react-i18next';
import { formatBytes } from '@/i18n/format';
import { IconButton, Progress } from '@/ui';
import { uploads, useUploads } from './useUploads';

/** G.B07 Upload tray (bottom right): background uploads; a row opens that video's processing page. */
export function UploadTray() {
  const { t, i18n } = useTranslation('library');
  const items = useUploads().filter((i) => i.phase !== 'cancelled');
  const navigate = useNavigate();
  if (items.length === 0) return null;

  return (
    <aside
      aria-label={t('tray.label')}
      className="fixed right-4 bottom-4 z-40 w-[320px] rounded-2xl border border-line bg-surface-1 p-3 shadow-lg"
    >
      <ul className="m-0 flex list-none flex-col gap-2 p-0">
        {items.map((it) => {
          const pct = it.size ? Math.round((100 * it.sent) / it.size) : 0;
          const open = () => {
            if (it.videoId && it.phase === 'done') {
              void navigate({ to: '/videos/$videoId/processing', params: { videoId: it.videoId } });
            }
          };
          return (
            <li key={it.id} className="grid gap-1">
              <div className="text-body-sm flex items-center gap-2">
                {it.phase === 'done' ? (
                  <CheckCircle2 size={16} className="text-accent" aria-hidden="true" />
                ) : it.phase === 'failed' ? (
                  <XCircle size={16} className="text-danger" aria-hidden="true" />
                ) : (
                  <Upload size={16} className="text-fg-3" aria-hidden="true" />
                )}
                <button
                  type="button"
                  className="min-w-0 flex-1 truncate bg-transparent p-0 text-left font-semibold text-fg"
                  onClick={open}
                  disabled={it.phase !== 'done'}
                >
                  {it.filename}
                </button>
                <span className="text-caption text-fg-3">
                  {it.phase === 'done'
                    ? t('tray.done')
                    : it.phase === 'failed'
                      ? t('tray.failed')
                      : `${pct}% · ${formatBytes(i18n.language, it.bytesPerSec)}/s`}
                </span>
                {(it.phase === 'done' || it.phase === 'failed') && (
                  <IconButton
                    size="sm"
                    label={t('tray.dismiss')}
                    icon={<X size={14} />}
                    onClick={() => uploads.dismiss(it.id)}
                  />
                )}
              </div>
              {it.phase !== 'done' && it.phase !== 'failed' && (
                <Progress
                  size="xs"
                  value={it.phase === 'completing' ? null : pct}
                  label={t('tray.progress', { name: it.filename })}
                />
              )}
            </li>
          );
        })}
      </ul>
    </aside>
  );
}
