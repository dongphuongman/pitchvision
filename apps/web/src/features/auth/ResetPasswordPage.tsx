import { useMutation } from '@tanstack/react-query';
import { getRouteApi, Link, useNavigate } from '@tanstack/react-router';
import { useEffect, useRef, useState, type FormEvent } from 'react';
import { useTranslation } from 'react-i18next';
import { isApiError } from '@/api/errors';
import { identityApi } from '@/api/identity';
import { useApiErrorMessage } from '@/api/useApiErrorMessage';
import { clearSession } from '@/auth/session';
import { MIN_PASSWORD_LENGTH, passwordStrength } from '@/lib/password';
import { Banner, Button, Field, PasswordInput, StrengthMeter, toast } from '@/ui';
import { AuthHeading, AuthLayout } from './AuthLayout';

const route = getRouteApi('/reset-password');

/** SCR-05 Reset password (`/reset-password?token=`). Expired / used token → E04 state. */
export function ResetPasswordPage() {
  const { t } = useTranslation('auth');
  const navigate = useNavigate();
  const { token } = route.useSearch();
  const [password, setPassword] = useState('');
  const [confirm, setConfirm] = useState('');
  const [passwordError, setPasswordError] = useState<string>();
  const [confirmError, setConfirmError] = useState<string>();
  const passwordRef = useRef<HTMLInputElement>(null);
  const confirmRef = useRef<HTMLInputElement>(null);
  const headingRef = useRef<HTMLHeadingElement>(null);
  const strength = passwordStrength(password);

  const reset = useMutation({
    mutationFn: () => identityApi.resetPassword(token ?? '', password),
    onSuccess: () => {
      // The server revoked every refresh token of the user: drop any local session too.
      clearSession();
      toast({ title: t('reset.success'), tone: 'success' });
      void navigate({ to: '/signin', replace: true });
    },
  });
  const expired =
    !token ||
    (isApiError(reset.error) &&
      (reset.error.code === 'TOKEN_EXPIRED' || reset.error.status === 410));
  const serverError = useApiErrorMessage(expired ? null : reset.error);

  useEffect(() => {
    (expired ? headingRef : passwordRef).current?.focus();
  }, [expired]);

  function onSubmit(e: FormEvent) {
    e.preventDefault();
    const pErr = !password
      ? t('validation.passwordRequired')
      : password.length < MIN_PASSWORD_LENGTH
        ? t('validation.passwordTooShort', { min: MIN_PASSWORD_LENGTH })
        : undefined;
    const cErr = !pErr && confirm !== password ? t('validation.passwordMismatch') : undefined;
    setPasswordError(pErr);
    setConfirmError(cErr);
    if (pErr) return passwordRef.current?.focus();
    if (cErr) return confirmRef.current?.focus();
    reset.mutate();
  }

  if (expired) {
    return (
      <AuthLayout>
        <Banner tone="warn">{t('reset.expiredBody')}</Banner>
        <AuthHeading ref={headingRef}>{t('reset.expiredTitle')}</AuthHeading>
        <Button asChild variant="primary" size="lg" className="w-full">
          <Link to="/forgot-password">{t('reset.requestNew')}</Link>
        </Button>
      </AuthLayout>
    );
  }

  return (
    <AuthLayout>
      <form className="flex flex-col gap-4" onSubmit={onSubmit} noValidate>
        <AuthHeading ref={headingRef} sub={t('reset.subtitle')}>
          {t('reset.title')}
        </AuthHeading>
        {serverError && <Banner tone="err">{serverError}</Banner>}
        <Field
          label={t('fields.newPassword')}
          error={passwordError}
          help={t('strength.help', {
            level: t(`strength.levels.${strength}`),
            min: MIN_PASSWORD_LENGTH,
          })}
        >
          <PasswordInput
            ref={passwordRef}
            name="new-password"
            autoComplete="new-password"
            value={password}
            onChange={(e) => setPassword(e.target.value)}
          />
        </Field>
        <StrengthMeter score={strength} className="-mt-2" />
        <Field label={t('fields.confirmPassword')} error={confirmError}>
          <PasswordInput
            ref={confirmRef}
            name="confirm-password"
            autoComplete="new-password"
            value={confirm}
            onChange={(e) => setConfirm(e.target.value)}
            onBlur={() =>
              confirm &&
              setConfirmError(confirm !== password ? t('validation.passwordMismatch') : undefined)
            }
          />
        </Field>
        <Button
          type="submit"
          variant="primary"
          size="lg"
          className="w-full"
          loading={reset.isPending}
        >
          {t('reset.submit')}
        </Button>
      </form>
    </AuthLayout>
  );
}
