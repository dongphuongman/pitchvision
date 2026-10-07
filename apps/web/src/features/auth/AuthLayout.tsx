import { Link } from '@tanstack/react-router';
import { forwardRef, type ReactNode } from 'react';
import { useTranslation } from 'react-i18next';
import { useQuery } from '@tanstack/react-query';
import { googleStartUrl, providersQuery } from '@/api/identity';
import { Logo } from '@/app/Logo';
import { cn } from '@/lib/cn';
import { Button, Card } from '@/ui';

/** Prototype `.auth`: pitch artwork + tagline on the left (≥ lg), the form column on the right. */
export function AuthLayout({ children }: { children: ReactNode }) {
  const { t } = useTranslation('auth');
  const { t: tc } = useTranslation();
  return (
    <div className="grid min-h-screen lg:grid-cols-[1.1fr_1fr]">
      <div className="relative hidden overflow-hidden bg-pitch lg:block" aria-hidden="true">
        <div className="absolute inset-0 bg-[linear-gradient(180deg,rgba(6,10,14,.2),rgba(6,10,14,.85))]" />
        <div className="absolute right-12 bottom-12 left-12 z-[2] text-white">
          <Logo label={tc('appName')} size="lg" />
          <p className="mt-[18px] mb-2 text-[34px] leading-[1.1] font-extrabold tracking-[-.03em]">
            {t('tagline')}
          </p>
          <p className="m-0 text-md text-white/75">{t('taglineBody')}</p>
        </div>
      </div>
      <main id="main" className="grid place-items-center bg-bg px-4 py-10 sm:px-10">
        <div className="flex w-full max-w-[400px] flex-col gap-4">
          <Link to="/" className="self-start rounded-md focus-ring lg:hidden">
            <Logo label={tc('appName')} />
          </Link>
          {children}
        </div>
      </main>
    </div>
  );
}

/** Page heading that receives focus on mount / state change (screen readers announce it). */
export const AuthHeading = forwardRef<HTMLHeadingElement, { children: ReactNode; sub?: ReactNode }>(
  function AuthHeading({ children, sub }, ref) {
    return (
      <div className="flex flex-col gap-2">
        <h1 ref={ref} tabIndex={-1} className="text-[26px] tracking-[-.02em] outline-none">
          {children}
        </h1>
        {sub && <p className="m-0 mb-1 text-fg-2">{sub}</p>}
      </div>
    );
  },
);

/** Prototype `.or` divider. */
export function OrDivider() {
  const { t } = useTranslation('auth');
  return (
    <div className="flex items-center gap-3 text-caption text-fg-3 before:h-px before:flex-1 before:bg-line after:h-px after:flex-1 after:bg-line">
      {t('or')}
    </div>
  );
}

/** "Continue with Google" + the "or" divider — only when the identity service has Google configured. */
export function GoogleSignIn({ label, returnTo }: { label: string; returnTo?: string }) {
  const providers = useQuery(providersQuery);
  if (providers.data?.google !== true) return null;
  return (
    <>
      <GoogleButton label={label} returnTo={returnTo} />
      <OrDivider />
    </>
  );
}

/** Prototype `.gbtn`: full-page navigation to the identity service's Google OIDC start. */
export function GoogleButton({ label, returnTo }: { label: string; returnTo?: string }) {
  return (
    <Button asChild size="lg" className="h-[42px] w-full">
      <a href={googleStartUrl(returnTo)}>
        <svg width="18" height="18" viewBox="0 0 48 48" aria-hidden="true">
          <path
            fill="#FFC107"
            d="M43.6 20.5H42V20H24v8h11.3C33.7 32.7 29.2 36 24 36c-6.6 0-12-5.4-12-12s5.4-12 12-12c3.1 0 5.8 1.2 7.9 3.1l5.7-5.7C34 6.1 29.3 4 24 4 12.9 4 4 12.9 4 24s8.9 20 20 20 20-8.9 20-20c0-1.3-.1-2.4-.4-3.5z"
          />
          <path
            fill="#FF3D00"
            d="m6.3 14.7 6.6 4.8C14.7 15.1 19 12 24 12c3.1 0 5.8 1.2 7.9 3.1l5.7-5.7C34 6.1 29.3 4 24 4 16.3 4 9.7 8.3 6.3 14.7z"
          />
          <path
            fill="#4CAF50"
            d="M24 44c5.2 0 9.9-2 13.4-5.2l-6.2-5.2C29.2 35.1 26.7 36 24 36c-5.2 0-9.6-3.3-11.3-8l-6.5 5C9.5 39.6 16.2 44 24 44z"
          />
          <path
            fill="#1976D2"
            d="M43.6 20.5H42V20H24v8h11.3c-.8 2.2-2.2 4.2-4.1 5.6l6.2 5.2C37 39.2 44 34 44 24c0-1.3-.1-2.4-.4-3.5z"
          />
        </svg>
        {label}
      </a>
    </Button>
  );
}

/** Prototype `.fi`: accent icon tile above a heading (inbox, link expired…). */
export function FeatureIcon({ children, className }: { children: ReactNode; className?: string }) {
  return (
    <div
      aria-hidden="true"
      className={cn(
        'grid size-[52px] place-items-center rounded-[10px] bg-[color-mix(in_srgb,var(--ac)_14%,transparent)] text-accent',
        className,
      )}
    >
      {children}
    </div>
  );
}

/** Prototype `.center > .card`: centred card for onboarding and invitations. */
export function CenteredCard({
  children,
  className,
  header,
}: {
  children: ReactNode;
  className?: string;
  header?: ReactNode;
}) {
  const { t: tc } = useTranslation();
  return (
    <main id="main" className="grid min-h-screen place-items-center bg-bg px-4 py-10 sm:px-10">
      <Card className={cn('flex w-full max-w-[560px] flex-col gap-[18px] p-6 sm:p-8', className)}>
        <div className="flex items-center gap-3">
          <Link to="/" className="rounded-md focus-ring">
            <Logo label={tc('appName')} />
          </Link>
          <div className="flex-1" />
          {header}
        </div>
        {children}
      </Card>
    </main>
  );
}

/** Prototype `.back` link style. */
export const backLinkClass =
  'inline-flex items-center gap-1.5 self-start rounded-sm text-control font-semibold text-fg-2 hover:text-fg focus-ring';

/** Inline text link (prototype `.lnk`). */
export const textLinkClass = 'rounded-sm font-semibold text-accent hover:underline focus-ring';
