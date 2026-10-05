import { Link } from '@tanstack/react-router';
import { Upload, Video } from 'lucide-react';
import { useTranslation } from 'react-i18next';
import { Page, PageHeader } from '@/app/Page';
import { RequireRole } from '@/auth/RequireRole';
import { Button, EmptyState } from '@/ui';
import { YoutubeIcon } from '@/ui/icons';
import { NewAnalysisDialog } from './NewAnalysisDialog';

const dialogState = { pvDialog: 'new' } as never;

/** SCR-10 Library (stub: empty state until the videos API lands in M1-FE-07). */
export function LibraryPage() {
  const { t } = useTranslation('library');
  return (
    <Page>
      <PageHeader title={t('title')} meta={t('count', { count: 0 })} />
      <EmptyState
        icon={<Video size={30} />}
        title={t('empty.title')}
        description={t('empty.body')}
        actions={
          <RequireRole min="editor">
            <Button asChild variant="primary" size="lg">
              <Link to="/library" search={{ new: 'youtube' }} state={dialogState}>
                <YoutubeIcon size={18} />
                {t('empty.youtube')}
              </Link>
            </Button>
            <Button asChild size="lg">
              <Link to="/library" search={{ new: 'upload' }} state={dialogState}>
                <Upload size={18} aria-hidden="true" />
                {t('empty.upload')}
              </Link>
            </Button>
          </RequireRole>
        }
      />
      <RequireRole min="editor">
        <NewAnalysisDialog />
      </RequireRole>
    </Page>
  );
}
