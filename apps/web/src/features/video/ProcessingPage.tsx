import { getRouteApi } from '@tanstack/react-router';
import { useTranslation } from 'react-i18next';
import { Page, PageHeader } from '@/app/Page';
import { Badge, Banner, Card, JOB_STATUS_TONE, Progress } from '@/ui';

const route = getRouteApi('/_app/videos/$videoId/processing');

/** SCR-13 Processing (stub). Live progress comes over WS topic job:{id} in M1-FE-13. */
export function ProcessingPage() {
  const { videoId } = route.useParams();
  const { t } = useTranslation('video');
  const { t: tc } = useTranslation();
  return (
    <Page>
      <PageHeader
        title={t('processing.title')}
        meta={<span className="font-mono text-caption">{videoId}</span>}
        actions={<Badge tone={JOB_STATUS_TONE.queued}>{tc('status.job.queued')}</Badge>}
      />
      <Card className="flex flex-col gap-4">
        <Progress value={null} label={t('processing.progress')} tone="info" />
        <Banner tone="info">{t('processing.body')}</Banner>
      </Card>
    </Page>
  );
}
