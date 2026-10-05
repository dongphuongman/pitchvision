import { Download } from 'lucide-react';
import { useTranslation } from 'react-i18next';
import { Page, PageHeader } from '@/app/Page';
import { EmptyState } from '@/ui';

/** SCR-26 Exports (stub). */
export function ExportsPage() {
  const { t } = useTranslation('video');
  return (
    <Page>
      <PageHeader title={t('exports.title')} />
      <EmptyState icon={<Download size={30} />} title={t('exports.empty')} variant="plain" />
    </Page>
  );
}
