import { Link } from '@tanstack/react-router';
import { Check, Clapperboard, Download, Map, Users } from 'lucide-react';
import type { ReactNode } from 'react';
import { useTranslation } from 'react-i18next';
import { Logo } from '@/app/Logo';
import { Badge, Button } from '@/ui';

const DOCS_URL = 'https://docs.pitchvision.ai';
const STATUS_URL = 'https://status.pitchvision.ai';
const SALES_URL = 'mailto:sales@pitchvision.ai';
const NEW_ANALYSIS = '/library?new=upload';

const FEATURES = [
  { key: 'broadcast', icon: <Clapperboard size={20} /> },
  { key: 'tracking', icon: <Users size={20} /> },
  { key: 'map', icon: <Map size={20} /> },
  { key: 'exports', icon: <Download size={20} /> },
] as const;

const PLANS = ['free', 'pro', 'team'] as const;

/**
 * SCR-01 Landing (public; signed-in visitors are sent to the library by the route). Message per
 * PLAN M1-FE-16: "Analyze any broadcast match". Pricing reuses the SCR-41 plan copy; Choose Pro
 * signs up with `plan=pro` (onboarding then opens SCR-41).
 */
export function LandingPage() {
  const { t } = useTranslation('landing');
  const { t: ts } = useTranslation('settings');
  const { t: tc } = useTranslation();

  return (
    <div className="min-h-screen bg-bg text-fg">
      <header className="sticky top-0 z-20 flex h-[64px] items-center gap-6 border-b border-line bg-bg/90 px-4 backdrop-blur sm:px-10">
        <a href="#top" className="rounded-md focus-ring">
          <Logo label={tc('appName')} />
        </a>
        <nav aria-label={t('nav.main')} className="hidden gap-1 md:flex">
          <NavLink href="#features">{t('nav.features')}</NavLink>
          <NavLink href="#pricing">{t('nav.pricing')}</NavLink>
          <NavLink href={DOCS_URL} external>
            {t('nav.docs')}
          </NavLink>
        </nav>
        <div className="flex-1" />
        <Button asChild variant="ghost">
          <Link to="/signin">{tc('signIn')}</Link>
        </Button>
        <Button asChild variant="primary">
          <Link to="/signup">{t('nav.getStarted')}</Link>
        </Button>
      </header>

      <main id="top">
        <section className="mx-auto grid max-w-[1180px] items-center gap-10 px-4 pt-14 pb-16 sm:px-10 lg:grid-cols-[1fr_1.1fr]">
          <div className="flex flex-col gap-5">
            <h1 className="m-0 text-[clamp(2.1rem,4.6vw,3.4rem)] leading-[1.05] font-extrabold tracking-[-0.02em]">
              {t('hero.title')}
            </h1>
            <p className="m-0 max-w-[520px] text-lg text-fg-2">{t('hero.subtitle')}</p>
            <p className="m-0 text-control font-semibold text-fg-3">{t('hero.chips')}</p>
            <div className="flex flex-wrap gap-3">
              <Button asChild variant="primary" size="lg">
                <Link to="/signup" search={{ return_to: NEW_ANALYSIS }}>
                  {t('hero.cta')}
                </Link>
              </Button>
              <Button asChild variant="ghost" size="lg">
                <a href="#how">{t('hero.secondary')}</a>
              </Button>
            </div>
          </div>
          <HeroDemo label={t('hero.demoLabel')} />
        </section>

        <section id="features" className="border-t border-line bg-surface-1">
          <div className="mx-auto max-w-[1180px] px-4 py-16 sm:px-10">
            <h2 className="mt-0 mb-8 text-2xl font-bold">{t('features.title')}</h2>
            <ul className="m-0 grid list-none gap-4 p-0 sm:grid-cols-2 lg:grid-cols-4">
              {FEATURES.map((f) => (
                <li
                  key={f.key}
                  className="flex flex-col gap-2 rounded-xl border border-line bg-surface-2 p-5"
                >
                  <span className="grid size-9 place-items-center rounded-lg bg-surface-3 text-accent">
                    {f.icon}
                  </span>
                  <h3 className="m-0 text-md font-bold">{t(`features.items.${f.key}.title`)}</h3>
                  <p className="m-0 text-control text-fg-2">{t(`features.items.${f.key}.body`)}</p>
                </li>
              ))}
            </ul>
            <div id="how" className="mt-12">
              <h2 className="mt-0 mb-5 text-2xl font-bold">{t('steps.title')}</h2>
              <ol className="m-0 grid list-none gap-4 p-0 md:grid-cols-3">
                {(['one', 'two', 'three'] as const).map((k, i) => (
                  <li key={k} className="flex items-start gap-3">
                    <span className="grid size-8 shrink-0 place-items-center rounded-full bg-accent font-bold text-white">
                      {i + 1}
                    </span>
                    <span className="pt-1 text-fg-2">{t(`steps.${k}`)}</span>
                  </li>
                ))}
              </ol>
            </div>
          </div>
        </section>

        <section id="pricing" className="mx-auto max-w-[1180px] px-4 py-16 sm:px-10">
          <h2 className="mt-0 mb-1 text-2xl font-bold">{t('pricing.title')}</h2>
          <p className="mt-0 mb-8 text-fg-2">{t('pricing.subtitle')}</p>
          <ul className="m-0 grid list-none gap-4 p-0 md:grid-cols-3">
            {PLANS.map((p) => (
              <li
                key={p}
                aria-label={ts(`upgrade.plans.${p}.name`)}
                className={`flex flex-col gap-3 rounded-xl border bg-surface-1 p-6 ${p === 'pro' ? 'border-accent' : 'border-line'}`}
              >
                <div className="flex items-center gap-2">
                  <b className="text-lg">{ts(`upgrade.plans.${p}.name`)}</b>
                  {p === 'pro' && <Badge tone="pro">{t('pricing.pro')}</Badge>}
                </div>
                <span className="text-2xl font-extrabold">{ts(`upgrade.plans.${p}.minutes`)}</span>
                <ul className="m-0 flex list-none flex-col gap-1.5 p-0 text-control text-fg-2">
                  {(['f1', 'f2'] as const).map((f) => (
                    <li key={f} className="flex items-center gap-2">
                      <Check size={15} className="text-accent" aria-hidden="true" />
                      {ts(`upgrade.plans.${p}.${f}`)}
                    </li>
                  ))}
                </ul>
                <div className="mt-auto pt-3">
                  {p === 'team' ? (
                    <Button asChild className="w-full">
                      <a href={SALES_URL}>{t('pricing.team')}</a>
                    </Button>
                  ) : (
                    <Button
                      asChild
                      variant={p === 'pro' ? 'primary' : 'secondary'}
                      className="w-full"
                    >
                      <Link to="/signup" search={{ plan: p }}>
                        {p === 'pro' ? t('pricing.pro') : t('pricing.free')}
                      </Link>
                    </Button>
                  )}
                </div>
              </li>
            ))}
          </ul>
          <p className="mt-4 mb-0 text-caption text-fg-3">{t('pricing.note')}</p>
        </section>
      </main>

      <footer className="border-t border-line px-4 py-6 sm:px-10">
        <div className="mx-auto flex max-w-[1180px] flex-wrap items-center gap-x-5 gap-y-2 text-control text-fg-3">
          <span>{t('footer.rights', { year: new Date().getFullYear() })}</span>
          <Link to="/terms" className="rounded-xs focus-ring hover:text-fg">
            {t('footer.terms')}
          </Link>
          <Link to="/privacy" className="rounded-xs focus-ring hover:text-fg">
            {t('footer.privacy')}
          </Link>
          <a
            href={STATUS_URL}
            target="_blank"
            rel="noopener noreferrer"
            className="rounded-xs focus-ring hover:text-fg"
          >
            {t('footer.status')}
          </a>
        </div>
      </footer>
    </div>
  );
}

function NavLink({
  href,
  external,
  children,
}: {
  href: string;
  external?: boolean;
  children: ReactNode;
}) {
  return (
    <a
      href={href}
      {...(external ? { target: '_blank', rel: 'noopener noreferrer' } : {})}
      className="rounded-md px-3 py-[7px] font-semibold text-fg-2 focus-ring hover:bg-surface-3 hover:text-fg"
    >
      {children}
    </a>
  );
}

const RED = '#e5484d';
const BLUE = '#3e8bff';
// [x, y, team] on the broadcast frame (perspective) and the same players on the 105 × 68 map
const FRAME: readonly [number, number, 'r' | 'b'][] = [
  [70, 150, 'r'], [130, 118, 'r'], [190, 170, 'b'], [240, 128, 'b'], [300, 182, 'r'],
  [340, 140, 'b'], [395, 110, 'r'], [430, 175, 'b'], [500, 132, 'r'], [540, 160, 'b'],
]; // prettier-ignore
const MAP: readonly [number, number, 'r' | 'b'][] = [
  [18, 30, 'r'], [30, 18, 'r'], [40, 44, 'b'], [50, 22, 'b'], [58, 50, 'r'],
  [64, 32, 'b'], [72, 14, 'r'], [78, 48, 'b'], [88, 26, 'r'], [94, 40, 'b'],
]; // prettier-ignore

/** Illustration (no video asset in M1): a broadcast frame with tracking boxes + the tactical map. */
function HeroDemo({ label }: { label: string }) {
  const color = (t: 'r' | 'b') => (t === 'r' ? RED : BLUE);
  return (
    <figure role="img" aria-label={label} className="m-0 grid gap-3 sm:grid-cols-[1.4fr_1fr]">
      <svg viewBox="0 0 600 260" className="w-full rounded-xl border border-line bg-[#1f6b3a]">
        <defs>
          <linearGradient id="grass" x1="0" y1="0" x2="0" y2="1">
            <stop offset="0" stopColor="#2a7d45" />
            <stop offset="1" stopColor="#1b5c33" />
          </linearGradient>
        </defs>
        <rect width="600" height="260" fill="url(#grass)" />
        <rect width="600" height="44" fill="#3b4652" />
        <g stroke="#e8f2ea" strokeOpacity=".7" strokeWidth="2" fill="none">
          <path d="M20 60 L580 60 L600 250 L0 250 Z" />
          <path d="M300 60 L300 250" />
          <ellipse cx="300" cy="150" rx="70" ry="38" />
        </g>
        {FRAME.map(([x, y, t], i) => (
          <g key={i}>
            <rect
              x={x - 9}
              y={y - 34}
              width="18"
              height="34"
              fill="none"
              stroke={color(t)}
              strokeWidth="2.5"
            />
            <rect x={x - 10} y={y - 48} width="26" height="12" fill="#05090dcc" />
            <text x={x - 7} y={y - 39} fontSize="9" fontWeight="700" fill={color(t)}>
              #{i + 3}
            </text>
          </g>
        ))}
        <circle cx="268" cy="152" r="5" fill="#fff" stroke="#0b0f14" strokeWidth="2" />
      </svg>
      <svg viewBox="-4 -4 113 76" className="w-full rounded-xl border border-line bg-[#245c3a]">
        <g stroke="#e8f2ea" strokeOpacity=".75" strokeWidth=".6" fill="none">
          <rect x="0" y="0" width="105" height="68" />
          <line x1="52.5" y1="0" x2="52.5" y2="68" />
          <circle cx="52.5" cy="34" r="9.15" />
          <rect x="0" y="13.84" width="16.5" height="40.32" />
          <rect x="88.5" y="13.84" width="16.5" height="40.32" />
        </g>
        {MAP.map(([x, y, t], i) => (
          <circle key={i} cx={x} cy={y} r="2" fill={color(t)} stroke="#0b0f14" strokeWidth=".4" />
        ))}
        <circle cx="47" cy="40" r="1.3" fill="#fff" />
      </svg>
    </figure>
  );
}
