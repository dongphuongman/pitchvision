import { Link, type ErrorComponentProps } from '@tanstack/react-router';
import { CircleAlert, Compass, Lock } from 'lucide-react';
import { useTranslation } from 'react-i18next';
import { Button, EmptyState, Spinner } from '@/ui';
import { Page } from './Page';

/** Shown while the session bootstrap / a route guard is still pending (after defaultPendingMs). */
export function FullPageSpinner() {
  const { t } = useTranslation();
  return (
    <div className="grid min-h-screen place-items-center bg-bg">
      <Spinner size={28} label={t('loadingApp')} />
    </div>
  );
}

/** SCR-50 404. */
export function NotFoundPage() {
  const { t } = useTranslation();
  return (
    <Page>
      <EmptyState
        icon={<Compass size={30} />}
        title={t('notFound.title')}
        description={t('notFound.body')}
        actions={
          <Button asChild variant="primary">
            <Link to="/library">{t('notFound.cta')}</Link>
          </Button>
        }
      />
    </Page>
  );
}

/** SCR-50 /error: unexpected render/loader error. */
export function ErrorPage({ reset }: ErrorComponentProps) {
  const { t } = useTranslation();
  return (
    <Page>
      <EmptyState
        icon={<CircleAlert size={30} />}
        title={t('error.title')}
        description={t('error.body')}
        actions={
          <Button variant="primary" onClick={reset}>
            {t('actions.retry')}
          </Button>
        }
      />
    </Page>
  );
}

/** SCR-50 /403: signed in, but the role doesn't allow the page. */
export function ForbiddenPage() {
  const { t } = useTranslation();
  return (
    <Page>
      <EmptyState
        icon={<Lock size={30} />}
        title={t('forbidden.title')}
        description={t('forbidden.body')}
        actions={
          <Button asChild variant="primary">
            <Link to="/library">{t('notFound.cta')}</Link>
          </Button>
        }
      />
    </Page>
  );
}
