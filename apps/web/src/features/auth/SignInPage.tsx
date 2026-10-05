import { useMutation } from '@tanstack/react-query';
import { getRouteApi, Link, useNavigate } from '@tanstack/react-router';
import { useEffect, useRef, useState, type FormEvent } from 'react';
import { useTranslation } from 'react-i18next';
import { isApiError } from '@/api/errors';
import { identityApi } from '@/api/identity';
import { useApiErrorMessage } from '@/api/useApiErrorMessage';
import { nextAfterAuth, safeReturnTo } from '@/auth/redirects';
import { applySession } from '@/auth/session';
import { isValidEmail } from '@/lib/password';
import { formatCountdown, useCountdown } from '@/lib/useCountdown';
import { Banner, Button, Field, Input, PasswordInput } from '@/ui';
import { AuthHeading, AuthLayout, GoogleButton, OrDivider, textLinkClass } from './AuthLayout';

const route = getRouteApi('/signin');

/** SCR-02 Sign in. */
export function SignInPage() {
  const { t } = useTranslation('auth');
  const navigate = useNavigate();
  const search = route.useSearch();
  const returnTo = safeReturnTo(search.return_to);

  const [email, setEmail] = useState(search.email ?? '');
  const [password, setPassword] = useState('');
  const [emailError, setEmailError] = useState<string>();
  const [passwordError, setPasswordError] = useState<string>();
  const emailRef = useRef<HTMLInputElement>(null);
  const passwordRef = useRef<HTMLInputElement>(null);
  const lockout = useCountdown();

  const login = useMutation({
    mutationFn: () => identityApi.login(email.trim(), password),
    onSuccess: (session) => {
      applySession(session);
      void navigate({ href: nextAfterAuth(session, returnTo), replace: true });
    },
    onError: (error) => {
      // UIUX E05: wrong credentials keep the email; lockout shows a countdown.
      setPassword('');
      const retryAfter = isApiError(error) ? error.problem.retry_after : undefined;
      if (isApiError(error) && error.code === 'RATE_LIMITED' && typeof retryAfter === 'number') {
        lockout.start(retryAfter);
      }
      passwordRef.current?.focus();
    },
  });
  const serverError = useApiErrorMessage(login.error);
  const locked = lockout.remaining > 0;

  useEffect(() => {
    emailRef.current?.focus();
  }, []);

  function validateEmail(value: string): string | undefined {
    if (!value.trim()) return t('validation.emailRequired');
    if (!isValidEmail(value)) return t('validation.emailInvalid');
    return undefined;
  }

  function onSubmit(e: FormEvent) {
    e.preventDefault();
    const eErr = validateEmail(email);
    const pErr = password ? undefined : t('validation.passwordRequired');
    setEmailError(eErr);
    setPasswordError(pErr);
    if (eErr) return emailRef.current?.focus();
    if (pErr) return passwordRef.current?.focus();
    login.mutate();
  }

  return (
    <AuthLayout>
      <form className="flex flex-col gap-4" onSubmit={onSubmit} noValidate>
        <AuthHeading sub={t('signIn.subtitle')}>{t('signIn.title')}</AuthHeading>
        <GoogleButton label={t('google.signIn')} returnTo={returnTo} />
        <OrDivider />
        {/* Banner tone="err" is role="alert": announced when it appears. */}
        {login.isError && (
          <Banner tone="err">
            {locked
              ? t('signIn.lockedOut', { time: formatCountdown(lockout.remaining) })
              : serverError}
          </Banner>
        )}
        <Field label={t('fields.email')} error={emailError}>
          <Input
            ref={emailRef}
            type="email"
            name="email"
            autoComplete="email"
            inputMode="email"
            value={email}
            onChange={(e) => setEmail(e.target.value)}
            onBlur={() => email && setEmailError(validateEmail(email))}
          />
        </Field>
        <Field label={t('fields.password')} error={passwordError}>
          <PasswordInput
            ref={passwordRef}
            name="password"
            autoComplete="current-password"
            value={password}
            onChange={(e) => setPassword(e.target.value)}
          />
        </Field>
        <div className="-mt-1 flex justify-end">
          <Link
            to="/forgot-password"
            search={{ email: email.trim() || undefined }}
            className={textLinkClass}
          >
            {t('signIn.forgot')}
          </Link>
        </div>
        <Button
          type="submit"
          variant="primary"
          size="lg"
          className="w-full"
          loading={login.isPending}
          disabled={locked}
        >
          {t('signIn.submit')}
        </Button>
        <p className="m-0 text-center text-control text-fg-2">
          {t('signIn.noAccount')}{' '}
          <Link to="/signup" search={{ return_to: returnTo }} className={textLinkClass}>
            {t('signIn.create')}
          </Link>
        </p>
      </form>
    </AuthLayout>
  );
}
