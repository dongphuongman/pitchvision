import { getRouteApi, Link } from '@tanstack/react-router';
import { useTranslation } from 'react-i18next';
import { Page, PageHeader } from '@/app/Page';
import { cn } from '@/lib/cn';
import { can } from '@/auth/roles';
import { useRole } from '@/auth/session';
import { Banner, Card } from '@/ui';
import { AboutSection } from './AboutSection';
import { BillingSection } from './BillingSection';
import { MembersSection } from './MembersSection';
import { OrganizationSection } from './OrganizationSection';
import { PreferencesSection } from './PreferencesSection';
import { PrivacySection } from './PrivacySection';
import { ProfileSection } from './ProfileSection';
import { SECTION_ROLE, SETTINGS_SECTIONS, type SettingsSection } from './sections';

function Body({ section }: { section: SettingsSection }) {
  const { t } = useTranslation('settings');
  switch (section) {
    case 'profile':
      return <ProfileSection />;
    case 'organization':
      return <OrganizationSection />;
    case 'members':
      return <MembersSection />;
    case 'billing':
      return <BillingSection />;
    case 'preferences':
      return <PreferencesSection />;
    case 'privacy':
      return <PrivacySection />;
    case 'about':
      return <AboutSection />;
    default:
      return (
        <Card>
          <p className="m-0 text-fg-2">{t(`soon.${section}`)}</p>
        </Card>
      );
  }
}

const route = getRouteApi('/_app/settings/$section');

/** SCR-40 Settings: left section nav (prototype `.set` / `.snav`) + section body. */
export function SettingsPage() {
  const { section } = route.useParams();
  const { t } = useTranslation('settings');
  const role = useRole();
  const visible = SETTINGS_SECTIONS.filter((s) => can(role, SECTION_ROLE[s]));
  const allowed = visible.includes(section);

  return (
    <Page>
      <PageHeader title={t('title')} />
      <div className="grid gap-8 md:grid-cols-[230px_1fr]">
        <nav aria-label={t('nav')} className="flex flex-col gap-0.5">
          {visible.map((s) => (
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
          {allowed ? <Body section={section} /> : <Banner tone="warn">{t('forbidden')}</Banner>}
        </section>
      </div>
    </Page>
  );
}
