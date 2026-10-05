import { useTranslation } from 'react-i18next';
import { API_BASE_URL } from '@/api/client';
import { useMeta } from '@/api/queries';
import { useApiErrorMessage } from '@/api/useApiErrorMessage';
import { Banner, Card, Skeleton } from '@/ui';

/** Settings → About: example of a typed query (`GET /api/v1/meta`) with problem+json errors. */
export function AboutSection() {
  const { t } = useTranslation('settings');
  const meta = useMeta();
  const errorMessage = useApiErrorMessage(meta.error);

  const rows: [string, React.ReactNode][] = [
    [t('about.web'), __APP_VERSION__],
    [
      t('about.api'),
      meta.isPending ? <Skeleton className="h-4 w-20" /> : (meta.data?.version ?? '—'),
    ],
    [
      t('about.schema'),
      meta.isPending ? <Skeleton className="h-4 w-8" /> : (meta.data?.schema_version ?? '—'),
    ],
    [t('about.apiUrl'), API_BASE_URL || t('about.sameOrigin')],
  ];

  return (
    <Card className="flex flex-col gap-4" aria-busy={meta.isPending}>
      {meta.isError && (
        <Banner tone="err">
          {t('about.unavailable')} — {errorMessage}
        </Banner>
      )}
      <dl className="m-0 grid grid-cols-[180px_1fr] gap-x-6 gap-y-2.5">
        {rows.map(([k, v]) => (
          <div key={k} className="contents">
            <dt className="text-fg-3">{k}</dt>
            <dd className="m-0 font-mono text-control tabular">{v}</dd>
          </div>
        ))}
      </dl>
    </Card>
  );
}
