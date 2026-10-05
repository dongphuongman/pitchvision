import { Link } from '@tanstack/react-router';
import { useTranslation } from 'react-i18next';
import { Logo } from '@/app/Logo';
import { useAuthStore } from '@/auth/session';
import { cn } from '@/lib/cn';
import { Badge, Banner, Button } from '@/ui';

export type LegalDoc = 'terms' | 'privacy';

// Section structure only. The wording is deliberately NOT written here: real terms come from
// counsel (UIUX SCR-53 is a static page; the prototype also uses placeholder copy).
const SECTIONS = {
  terms: [
    'acceptance',
    'service',
    'accounts',
    'content',
    'analysis',
    'dataLocation',
    'acceptableUse',
    'plans',
    'termination',
    'liability',
    'changes',
    'contact',
  ],
  privacy: [
    'controller',
    'data',
    'purposes',
    'residency',
    'processors',
    'retention',
    'security',
    'rights',
    'cookies',
    'children',
    'changes',
    'contact',
  ],
} as const;

const DOC_PATH = { terms: '/terms', privacy: '/privacy' } as const;

/** SCR-53 Legal (`/terms`, `/privacy`) — public static page, marked DRAFT. */
export function LegalPage({ doc }: { doc: LegalDoc }) {
  const { t } = useTranslation('legal');
  const { t: tc } = useTranslation();
  const signedIn = useAuthStore((s) => s.status === 'authenticated');
  const sections = SECTIONS[doc];

  return (
    <div className="min-h-screen bg-bg">
      <header className="flex h-[68px] items-center gap-7 border-b border-line px-4 sm:px-12">
        <Link to="/" className="rounded-md focus-ring">
          <Logo label={tc('appName')} />
        </Link>
        <div className="flex-1" />
        <Button asChild variant="ghost">
          {signedIn ? (
            <Link to="/library">{tc('goToLibrary')}</Link>
          ) : (
            <Link to="/signin">{tc('signIn')}</Link>
          )}
        </Button>
      </header>
      <div className="mx-auto grid max-w-[1100px] gap-10 px-4 pt-[26px] pb-[60px] sm:px-8 md:grid-cols-[220px_1fr]">
        <aside className="flex flex-col gap-6">
          <nav aria-label={t('nav')} className="flex flex-col gap-0.5">
            {(Object.keys(DOC_PATH) as LegalDoc[]).map((d) => (
              <Link
                key={d}
                to={DOC_PATH[d]}
                aria-current={d === doc ? 'page' : undefined}
                className={cn(
                  'rounded-[9px] px-3 py-[9px] font-semibold text-fg-2 focus-ring hover:bg-surface-3 hover:text-fg',
                  d === doc && 'bg-surface-3 text-fg',
                )}
              >
                {t(`${d}.title`)}
              </Link>
            ))}
          </nav>
          <nav aria-label={t('toc')} className="hidden flex-col gap-1 text-control md:flex">
            <p className="m-0 mb-1 text-xs font-bold tracking-[.06em] text-fg-3 uppercase">
              {t('toc')}
            </p>
            {sections.map((s, i) => (
              <a key={s} href={`#${s}`} className="rounded-sm text-fg-2 focus-ring hover:text-fg">
                {i + 1}. {t(`${doc}.sections.${s}` as `terms.sections.acceptance`)}
              </a>
            ))}
          </nav>
        </aside>
        <article className="max-w-[70ch]">
          <Banner tone="warn" className="mb-5">
            {t('draft')}
          </Banner>
          <div className="mb-1.5 flex items-center gap-3">
            <h1 className="text-[30px]">{t(`${doc}.title`)}</h1>
            <Badge tone="warn">{t('draftBadge')}</Badge>
          </div>
          <p className="m-0 text-fg-3">{t('status')}</p>
          {sections.map((s, i) => (
            <section key={s} id={s} aria-labelledby={`${s}-h`} className="scroll-mt-6">
              <h2 id={`${s}-h`} className="mt-[26px] mb-2 text-lg">
                {i + 1}. {t(`${doc}.sections.${s}` as `terms.sections.acceptance`)}
              </h2>
              <p className="m-0 text-fg-2 italic">{t('placeholder')}</p>
            </section>
          ))}
        </article>
      </div>
    </div>
  );
}
