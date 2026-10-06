import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { useNavigate } from '@tanstack/react-router';
import { Bell } from 'lucide-react';
import { useTranslation } from 'react-i18next';
import {
  markNotificationsRead,
  notificationsKey,
  notificationsQuery,
  type AppNotification,
} from '@/api/usage';
import { useFormat } from '@/i18n/format';
import { cn } from '@/lib/cn';
import {
  DropdownMenu,
  DropdownMenuContent,
  DropdownMenuItem,
  DropdownMenuSeparator,
  DropdownMenuTrigger,
  IconButton,
} from '@/ui';

/** SCR-51 Notification center: bell + unread badge (G.E07), newest 20, mark all read (E01), open (E02). */
export function NotificationMenu() {
  const { t } = useTranslation();
  const fmt = useFormat();
  const navigate = useNavigate();
  const qc = useQueryClient();
  const { data } = useQuery(notificationsQuery);
  const read = useMutation({
    mutationFn: markNotificationsRead,
    onSettled: () => void qc.invalidateQueries({ queryKey: notificationsKey }),
  });
  const unread = data?.unread_count ?? 0;
  const items = data?.items ?? [];

  function text(n: AppNotification): string {
    const d = n.data as Record<string, unknown>;
    return t(`notifications.types.${n.type}`, {
      title: n.video_title ?? t('notifications.untitled'),
      filename: typeof d.filename === 'string' ? d.filename : '',
    });
  }

  function open(n: AppNotification) {
    if (!n.read_at) read.mutate({ ids: [n.id] });
    void navigate({ to: n.path });
  }

  return (
    <DropdownMenu>
      <DropdownMenuTrigger asChild>
        <span className="relative inline-flex">
          <IconButton
            label={
              unread
                ? `${t('nav.notifications')} · ${t('notifications.unread', { count: unread })}`
                : t('nav.notifications')
            }
            icon={<Bell size={18} />}
          />
          {unread > 0 && (
            <span
              aria-hidden="true"
              className="pointer-events-none absolute -top-0.5 -right-0.5 grid min-w-4 place-items-center rounded-full bg-danger px-1 text-[10px] font-bold text-white"
            >
              {unread > 9 ? '9+' : unread}
            </span>
          )}
        </span>
      </DropdownMenuTrigger>
      <DropdownMenuContent className="w-[380px]" align="end">
        <div className="flex items-center justify-between px-2.5 py-1.5">
          <span className="text-control font-semibold">{t('notifications.title')}</span>
          {unread > 0 && (
            <button
              type="button"
              className="rounded-xs text-xs font-semibold text-accent focus-ring hover:underline"
              onClick={() => read.mutate({ all: true })}
            >
              {t('notifications.markAllRead')}
            </button>
          )}
        </div>
        <DropdownMenuSeparator />
        {items.length === 0 ? (
          <p className="m-0 px-2.5 py-6 text-center text-control text-fg-3">
            {t('nav.noNotifications')}
          </p>
        ) : (
          items.map((n) => (
            <DropdownMenuItem key={n.id} onSelect={() => open(n)} className="items-start">
              <span
                aria-hidden="true"
                className={cn(
                  'mt-1.5 size-2 shrink-0 rounded-full',
                  n.read_at ? 'bg-transparent' : 'bg-accent',
                )}
              />
              <span className="flex min-w-0 flex-1 flex-col">
                <span className={cn('truncate', !n.read_at && 'font-semibold text-fg')}>
                  {text(n)}
                </span>
                <span className="text-xs text-fg-3">{fmt.relative(n.created_at)}</span>
              </span>
            </DropdownMenuItem>
          ))
        )}
      </DropdownMenuContent>
    </DropdownMenu>
  );
}
