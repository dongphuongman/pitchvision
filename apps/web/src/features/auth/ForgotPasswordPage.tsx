import { useMutation } from '@tanstack/react-query';
import { getRouteApi, Link } from '@tanstack/react-router';
import { ArrowLeft, Mail } from 'lucide-react';
import { useEffect, useRef, useState, type FormEvent } from 'react';
import { Trans, useTranslation } from 'react-i18next';
import { isApiError } from '@/api/errors';
import { identityApi } from '@/api/identity';
import { useApiErrorMessage } from '@/api/useApiErrorMessage';
import { isValidEmail } from '@/lib/password';
import { formatCountdown, useCountdown } from '@/lib/useCountdown';
import { useFocusOnChange } from '@/lib/useFocusOnChange';
import { Banner, Button, Field, Input, toast } from '@/ui';
import { AuthHeading, AuthLayout, backLinkClass, FeatureIcon, textLinkClass } from './AuthLayout';

const route = getRouteApi('/forgot-password');
export const RESEND_COOLDOWN_S = 60;

/**
 * SCR-04 Forgot password. Always shows the same "if an account exists" message (no account
 * enumeration); only a network failure is reported as an error.
 */
export function ForgotPasswordPage() {
  const { t } = useTranslation('auth');
  const search = route.useSearch();
  const [email, setEmail] = useState(search.email ?? '');
  const [emailError, setEmailError] = useState<string>();
  const [sentTo, setSentTo] = useState<string>();
  const cooldown = useCountdown();
  const emailRef = useRef<HTMLInputElement>(null);
  const headingRef = useFocusOnChange<HTMLHeadingElement>(sentTo !== undefined);

  useEffect(() => {
    emailRef.current?.focus();
  }, []);

  const send = useMutation({
    mutationFn: async (address: string) => {
      try {
        await identityApi.forgotPassword(address);
      } catch (e) {
        // Same UI for any server answer (the contract says always 202 anyway).
        if (isApiError(e) && e.status === 0) throw e;
      }
      return address;
    },
    onSuccess: (address) => {
      if (sentTo !== undefined) toast({ title: t('forgot.resent'), tone: 'success' });
      setSentTo(address);
      cooldown.start(RESEND_COOLDOWN_S);
    },
  });
  const networkError = useApiErrorMessage(send.error);

  function onSubmit(e: FormEvent) {
    e.preventDefault();
    const value = email.trim();
    const err = !value
      ? t('validation.emailRequired')
      : !isValidEmail(value)
        ? t('validation.emailInvalid')
        : undefined;
    setEmailError(err);
    if (err) return emailRef.current?.focus();
    send.mutate(value);
  }

  const back = (
    <Link to="/signin" className={backLinkClass}>
      <ArrowLeft size={16} aria-hidden="true" />
      {t('forgot.back')}
    </Link>
  );

  if (sentTo !== undefined) {
    return (
      <AuthLayout>
        <FeatureIcon>
          <Mail size={22} />
        </FeatureIcon>
        <AuthHeading ref={headingRef}>{t('forgot.sentTitle')}</AuthHeading>
        <p className="-mt-2 mb-1 text-fg-2" role="status">
          <Trans
            t={t}
            i18nKey="forgot.sentBody"
            values={{ email: sentTo }}
            components={{ b: <b className="text-fg" /> }}
          />
        </p>
        {networkError && <Banner tone="err">{networkError}</Banner>}
        <p className="m-0 text-fg-2">
          {t('forgot.notReceived')}{' '}
          {cooldown.remaining > 0 ? (
            <span className="text-fg-3">
              {t('forgot.resendIn', { time: formatCountdown(cooldown.remaining) })}
            </span>
          ) : (
            <button
              type="button"
              className={textLinkClass}
              disabled={send.isPending}
              onClick={() => send.mutate(sentTo)}
            >
              {t('forgot.resend')}
            </button>
          )}
        </p>
        {back}
      </AuthLayout>
    );
  }

  return (
    <AuthLayout>
      <form className="flex flex-col gap-4" onSubmit={onSubmit} noValidate>
        <AuthHeading ref={headingRef} sub={t('forgot.subtitle')}>
          {t('forgot.title')}
        </AuthHeading>
        {networkError && <Banner tone="err">{networkError}</Banner>}
        <Field label={t('fields.email')} error={emailError}>
          <Input
            ref={emailRef}
            type="email"
            name="email"
            autoComplete="email"
            inputMode="email"
            value={email}
            onChange={(e) => setEmail(e.target.value)}
          />
        </Field>
        <Button
          type="submit"
          variant="primary"
          size="lg"
          className="w-full"
          loading={send.isPending}
        >
          {t('forgot.submit')}
        </Button>
        {back}
      </form>
    </AuthLayout>
  );
}
