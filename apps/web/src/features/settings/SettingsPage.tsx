import { getRouteApi, Link } from '@tanstack/react-router';
import { useTranslation } from 'react-i18next';
import { Page, PageHeader } from '@/app/Page';
import { cn } from '@/lib/cn';
import { Card } from '@/ui';
import { AboutSection } from './AboutSection';
import { PreferencesSection } from './PreferencesSection';
import { SETTINGS_SECTIONS } from './sections';

const route = getRouteApi('/_app/settings/$section');

/** SCR-40 Settings: left section nav (prototype `.set` / `.snav`) + section body. */
export function SettingsPage() {
  const { section } = route.useParams();
  const { t } = useTranslation('settings');

  return (
    <Page>
      <PageHeader title={t('title')} />
      <div className="grid gap-8 md:grid-cols-[230px_1fr]">
        <nav aria-label={t('nav')} className="flex flex-col gap-0.5">
          {SETTINGS_SECTIONS.map((s) => (
            <Link
              key={s}
              to="/settings/$section"
              params={{ section: s }}
              className={cn(
                'flex items-center gap-2.5 rounded-[9px] px-3 py-[9px] font-semibold text-fg-2 focus-ring hover:bg-surface-3 hover:text-fg',
                s === section && 'bg-surface-3 text-fg',
              )}
              aria-current={s === section ? 'page' : undefined}
            >
              {t(`sections.${s}`)}
            </Link>
          ))}
        </nav>
        <section className="flex max-w-[760px] flex-col gap-[18px]">
          <h2>{t(`sections.${section}`)}</h2>
          {section === 'preferences' ? (
            <PreferencesSection />
          ) : section === 'about' ? (
            <AboutSection />
          ) : (
            <Card>
              <p className="m-0 text-fg-2">{t('placeholder')}</p>
            </Card>
          )}
        </section>
      </div>
    </Page>
  );
}
