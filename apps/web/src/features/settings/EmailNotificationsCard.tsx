import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { useTranslation } from 'react-i18next';
import { identityApi, type EmailPrefs, type Me } from '@/api/identity';
import { Card, Skeleton, Switch, toast } from '@/ui';

const KEYS = ['job_ready', 'job_failed', 'export_ready'] as const;
const ME_KEY = ['identity', 'me'] as const;

/** SCR-40 Preferences R03: email for job done / failed / export ready (saved on toggle). */
export function EmailNotificationsCard() {
  const { t } = useTranslation('settings');
  const qc = useQueryClient();
  const me = useQuery({ queryKey: ME_KEY, queryFn: ({ signal }) => identityApi.me(signal) });
  const save = useMutation({
    mutationFn: (prefs: EmailPrefs) => identityApi.updateMe({ email_prefs: prefs }),
    onMutate: (prefs) => {
      qc.setQueryData<Me>(ME_KEY, (old) =>
        old ? { ...old, email_prefs: { ...old.email_prefs, ...prefs } } : old,
      );
    },
    onSuccess: (data) => {
      qc.setQueryData(ME_KEY, data);
      toast({ title: t('notifications.saved'), tone: 'success' });
    },
    onError: () => void qc.invalidateQueries({ queryKey: ME_KEY }),
  });

  return (
    <Card className="flex flex-col gap-3">
      <b>{t('notifications.title')}</b>
      {me.isPending ? (
        <Skeleton className="h-20" />
      ) : (
        KEYS.map((k) => (
          <Switch
            key={k}
            label={t(`notifications.${k}`)}
            checked={me.data?.email_prefs?.[k] ?? true}
            onCheckedChange={(v) => save.mutate({ [k]: v })}
          />
        ))
      )}
      <p className="m-0 text-caption text-fg-3">{t('notifications.help')}</p>
    </Card>
  );
}
