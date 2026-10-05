import { useMutation, useQuery } from '@tanstack/react-query';
import { getRouteApi, Link, useNavigate } from '@tanstack/react-router';
import { Mail, MailWarning } from 'lucide-react';
import { useEffect, useState } from 'react';
import { Trans, useTranslation } from 'react-i18next';
import { identityApi, type Session } from '@/api/identity';
import { useApiErrorMessage } from '@/api/useApiErrorMessage';
import { nextAfterAuth, safeReturnTo } from '@/auth/redirects';
import { applySession, clearSession, refreshSession, signOut, useAuthStore } from '@/auth/session';
import { formatCountdown, useCountdown } from '@/lib/useCountdown';
import { useFocusOnChange } from '@/lib/useFocusOnChange';
import { Banner, Button, Spinner, toast } from '@/ui';
import { AuthHeading, AuthLayout, FeatureIcon, textLinkClass } from './AuthLayout';
import { RESEND_COOLDOWN_S } from './ForgotPasswordPage';

const route = getRouteApi('/verify-email');

/** SCR-06 polls the verification state this often (UIUX: "polling 5s"). */
export const VERIFY_POLL_MS = 5_000;

// Email tokens are single-use: StrictMode / remounts must not POST the same token twice.
const verifications = new Map<string, Promise<Session>>();
function verifyOnce(token: string): Promise<Session> {
  let p = verifications.get(token);
  if (!p) {
    p = identityApi.verifyEmail(token);
    verifications.set(token, p);
  }
  return p;
}

const WEBMAIL: { test: RegExp; key: 'openGmail' | 'openOutlook'; url: string }[] = [
  { test: /@(gmail|googlemail)\.com$/i, key: 'openGmail', url: 'https://mail.google.com/' },
  {
    test: /@(outlook|hotmail|live|msn)\.[a-z.]+$/i,
    key: 'openOutlook',
    url: 'https://outlook.live.com/mail/',
  },
];

/** SCR-06 Verify email: `/verify-email` (inbox + polling) or `/verify-email?token=` (link). */
export function VerifyEmailPage() {
  const { token } = route.useSearch();
  return token ? <VerifyToken token={token} /> : <CheckInbox />;
}

function VerifyToken({ token }: { token: string }) {
  const { t } = useTranslation('auth');
  const navigate = useNavigate();
  const { return_to } = route.useSearch();
  const [failed, setFailed] = useState(false);
  const headingRef = useFocusOnChange<HTMLHeadingElement>(failed);

  useEffect(() => {
    let alive = true;
    verifyOnce(token).then(
      (session) => {
        if (!alive) return;
        applySession(session);
        toast({ title: t('verify.verified'), tone: 'success' });
        void navigate({ href: nextAfterAuth(session, return_to), replace: true });
      },
      () => alive && setFailed(true),
    );
    return () => {
      alive = false;
    };
  }, [token, return_to, navigate, t]);

  if (!failed) {
    return (
      <AuthLayout>
        <div className="flex items-center gap-3" role="status">
          <Spinner label={null} size={20} />
          <span className="text-md text-fg-2">{t('verify.verifying')}</span>
        </div>
      </AuthLayout>
    );
  }
  return (
    <AuthLayout>
      <FeatureIcon>
        <MailWarning size={24} />
      </FeatureIcon>
      <AuthHeading ref={headingRef} sub={t('verify.expiredBody')}>
        {t('verify.expiredTitle')}
      </AuthHeading>
      <Button asChild variant="primary" size="lg" className="w-full">
        <Link to="/signin" search={{ return_to: '/verify-email' }}>
          {t('verify.signIn')}
        </Link>
      </Button>
    </AuthLayout>
  );
}

function CheckInbox() {
  const { t } = useTranslation('auth');
  const navigate = useNavigate();
  const { return_to } = route.useSearch();
  const returnTo = safeReturnTo(return_to);
  const user = useAuthStore((s) => s.user);
  const email = user?.email ?? '';
  const cooldown = useCountdown();

  // Detects verification done on another device (the link opens wherever the mail is read).
  const me = useQuery({
    queryKey: ['identity', 'me', 'verify-poll'],
    queryFn: ({ signal }) => identityApi.me(signal),
    refetchInterval: (q) => (q.state.data?.email_verified ? false : VERIFY_POLL_MS),
    refetchIntervalInBackground: true,
    staleTime: 0,
    gcTime: 0,
  });
  const verified = me.data?.email_verified === true;

  useEffect(() => {
    if (!verified) return;
    let alive = true;
    // New access token with email_verified=true (and any memberships) before moving on.
    void refreshSession().then((session) => {
      if (!alive) return;
      toast({ title: t('verify.verified'), tone: 'success' });
      const state = session ?? useAuthStore.getState();
      void navigate({ href: nextAfterAuth(state, returnTo), replace: true });
    });
    return () => {
      alive = false;
    };
  }, [verified, navigate, returnTo, t]);

  const resend = useMutation({
    mutationFn: () => identityApi.resendVerification(),
    onSuccess: () => {
      toast({ title: t('verify.resent'), tone: 'success' });
      cooldown.start(RESEND_COOLDOWN_S);
    },
  });
  const changeEmail = useMutation({
    mutationFn: () => identityApi.deleteUnverified(),
    onSuccess: () => {
      const name = user?.name;
      clearSession();
      void navigate({ to: '/signup', search: { name, return_to: returnTo }, replace: true });
    },
  });
  const error = useApiErrorMessage(resend.error ?? changeEmail.error);
  const webmail = WEBMAIL.find((w) => w.test.test(email));

  async function onSignOut() {
    await signOut();
    void navigate({ to: '/signin', replace: true });
  }

  return (
    <AuthLayout>
      <FeatureIcon>
        <Mail size={24} />
      </FeatureIcon>
      <AuthHeading>{t('verify.title')}</AuthHeading>
      <p className="-mt-2 mb-1 text-fg-2">
        <Trans
          t={t}
          i18nKey="verify.body"
          values={{ email }}
          components={{ b: <b className="text-fg" /> }}
        />
      </p>
      <p className="m-0 flex items-center gap-2 text-caption text-fg-3" role="status">
        <Spinner size={14} label={null} />
        {t('verify.waiting')}
      </p>
      {error && <Banner tone="err">{error}</Banner>}
      {webmail && (
        <div className="flex gap-2">
          <Button asChild>
            <a href={webmail.url} target="_blank" rel="noopener noreferrer">
              {t(`verify.${webmail.key}`)}
            </a>
          </Button>
        </div>
      )}
      <p className="m-0 text-fg-2">
        {t('verify.notReceived')}{' '}
        {cooldown.remaining > 0 ? (
          <span className="text-fg-3">
            {t('verify.resendIn', { time: formatCountdown(cooldown.remaining) })}
          </span>
        ) : (
          <button
            type="button"
            className={textLinkClass}
            disabled={resend.isPending}
            onClick={() => resend.mutate()}
          >
            {t('verify.resend')}
          </button>
        )}
      </p>
      <p className="m-0 text-fg-2">
        {t('verify.wrongEmail')}{' '}
        <button
          type="button"
          className={textLinkClass}
          disabled={changeEmail.isPending}
          onClick={() => changeEmail.mutate()}
        >
          {t('verify.changeIt')}
        </button>
      </p>
      <hr className="my-1 border-line" />
      <button
        type="button"
        className="self-start rounded-sm font-semibold text-fg-2 focus-ring hover:text-fg"
        onClick={() => void onSignOut()}
      >
        {t('verify.signOut')}
      </button>
    </AuthLayout>
  );
}
