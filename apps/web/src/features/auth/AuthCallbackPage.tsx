import { Link, useNavigate, useRouterState } from '@tanstack/react-router';
import { CircleAlert } from 'lucide-react';
import { useEffect, useState } from 'react';
import { useTranslation } from 'react-i18next';
import { parseCallbackFragment } from '@/auth/callback';
import { nextAfterAuth } from '@/auth/redirects';
import { refreshSession, useAuthStore } from '@/auth/session';
import { useFocusOnChange } from '@/lib/useFocusOnChange';
import { Button, Spinner } from '@/ui';
import { AuthHeading, AuthLayout, FeatureIcon } from './AuthLayout';

const KNOWN_ERRORS = new Set(['UNAUTHENTICATED', 'EMAIL_NOT_VERIFIED']);

/**
 * `/auth/callback#…` — landing page after Google OIDC. Identity contract (W2 clarification):
 * success `#status=ok&new_user=<bool>&return_to=<path>` with the `pv_refresh` cookie set;
 * failure `#error=UNAUTHENTICATED|EMAIL_NOT_VERIFIED`. No tokens in URLs: the Session comes
 * from `POST /auth/refresh`. The fragment is wiped from history immediately. `return_to` is
 * re-sanitised here (nextAfterAuth); new users without an org land on onboarding.
 */
export function AuthCallbackPage() {
  const { t } = useTranslation('auth');
  const { t: te } = useTranslation('errors');
  const navigate = useNavigate();
  const hash = useRouterState({ select: (s) => s.location.hash });
  const [fragment] = useState(() => parseCallbackFragment(hash));
  const [refreshFailed, setRefreshFailed] = useState(false);
  const failed = fragment.error !== undefined || refreshFailed;
  const headingRef = useFocusOnChange<HTMLHeadingElement>(failed);

  useEffect(() => {
    // Don't leave fragment data in the history entry.
    if (window.location.hash) {
      window.history.replaceState(window.history.state, '', window.location.pathname);
    }
    if (fragment.error !== undefined) return;
    let alive = true;
    const current = useAuthStore.getState();
    const session =
      current.status === 'authenticated' ? Promise.resolve(current) : refreshSession();
    void session.then((s) => {
      if (!alive) return;
      if (!s) {
        setRefreshFailed(true);
        return;
      }
      void navigate({ href: nextAfterAuth(s, fragment.returnTo), replace: true });
    });
    return () => {
      alive = false;
    };
  }, [fragment, navigate]);

  if (!failed) {
    return (
      <AuthLayout>
        <div className="flex items-center gap-3" role="status">
          <Spinner label={null} size={20} />
          <span className="text-md text-fg-2">{t('callback.signingIn')}</span>
        </div>
      </AuthLayout>
    );
  }
  const code = fragment.error;
  return (
    <AuthLayout>
      <FeatureIcon className="bg-[color-mix(in_srgb,var(--dan)_14%,transparent)] text-danger">
        <CircleAlert size={24} />
      </FeatureIcon>
      <AuthHeading
        ref={headingRef}
        sub={
          code && KNOWN_ERRORS.has(code)
            ? te(`codes.${code as 'UNAUTHENTICATED' | 'EMAIL_NOT_VERIFIED'}`)
            : t('callback.failedBody')
        }
      >
        {t('callback.failedTitle')}
      </AuthHeading>
      <Button asChild variant="primary" size="lg" className="w-full">
        <Link to="/signin">{t('callback.back')}</Link>
      </Button>
    </AuthLayout>
  );
}
