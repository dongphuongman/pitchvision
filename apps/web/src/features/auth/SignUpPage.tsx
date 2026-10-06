import { useMutation, useQuery } from '@tanstack/react-query';
import { getRouteApi, Link, useNavigate } from '@tanstack/react-router';
import { useRef, useState, type FormEvent, type ReactNode } from 'react';
import { Trans, useTranslation } from 'react-i18next';
import { isApiError } from '@/api/errors';
import { identityApi } from '@/api/identity';
import { useApiErrorMessage } from '@/api/useApiErrorMessage';
import { nextAfterAuth, safeReturnTo } from '@/auth/redirects';
import { applySession } from '@/auth/session';
import { isValidEmail, MIN_PASSWORD_LENGTH, passwordStrength } from '@/lib/password';
import { rememberSignupPlan } from '@/lib/signupPlan';
import { Banner, Button, Checkbox, Field, Input, PasswordInput, StrengthMeter } from '@/ui';
import { AuthHeading, AuthLayout, GoogleButton, OrDivider, textLinkClass } from './AuthLayout';

const route = getRouteApi('/signup');

type FieldErrors = Partial<Record<'name' | 'email' | 'password', ReactNode>>;

/** SCR-03 Sign up (`/signup?invite=&plan=&name=&return_to=`). */
export function SignUpPage() {
  const { t } = useTranslation('auth');
  const { t: tc } = useTranslation();
  const { t: te } = useTranslation('errors');
  const navigate = useNavigate();
  const search = route.useSearch();
  const returnTo = safeReturnTo(search.return_to);
  const inviteToken = search.invite;

  // Coming from SCR-52: the email is locked to the invited address.
  const invite = useQuery({
    queryKey: ['identity', 'invite', inviteToken],
    queryFn: ({ signal }) => identityApi.getInvite(inviteToken ?? '', signal),
    enabled: Boolean(inviteToken),
    retry: false,
  });
  const lockedEmail = invite.data && !invite.data.expired ? invite.data.email : undefined;

  const [name, setName] = useState(search.name ?? '');
  const [emailInput, setEmail] = useState('');
  const email = lockedEmail ?? emailInput;
  const [password, setPassword] = useState('');
  const [agreed, setAgreed] = useState(false);
  const [errors, setErrors] = useState<FieldErrors>({});
  const nameRef = useRef<HTMLInputElement>(null);
  const emailRef = useRef<HTMLInputElement>(null);
  const passwordRef = useRef<HTMLInputElement>(null);
  const strength = passwordStrength(password);

  const register = useMutation({
    mutationFn: () =>
      identityApi.register({
        name: name.trim(),
        email: email.trim(),
        password,
        ...(lockedEmail && inviteToken ? { invite_token: inviteToken } : {}),
        ...(search.plan ? { plan: search.plan } : {}),
      }),
    onSuccess: (session) => {
      applySession(session);
      rememberSignupPlan(search.plan);
      // Invited with a matching email ⇒ already verified and a member: skip onboarding 1–2.
      const joinedViaInvite = Boolean(lockedEmail) && session.user.email_verified;
      const href =
        joinedViaInvite && session.memberships.length > 0
          ? `/onboarding/preferences${returnTo ? `?${new URLSearchParams({ return_to: returnTo }).toString()}` : ''}`
          : nextAfterAuth(session, returnTo);
      void navigate({ href, replace: true });
    },
    onError: (error) => {
      if (isApiError(error) && error.code === 'EMAIL_TAKEN') {
        setErrors({
          email: (
            <>
              {te('codes.EMAIL_TAKEN')}{' '}
              <Link
                to="/signin"
                search={{ return_to: returnTo, email: email.trim() }}
                className={textLinkClass}
              >
                {t('signUp.signInInstead')}
              </Link>
            </>
          ),
        });
        emailRef.current?.focus();
      }
    },
  });
  const emailTaken = isApiError(register.error) && register.error.code === 'EMAIL_TAKEN';
  const serverError = useApiErrorMessage(emailTaken ? null : register.error);

  function validate(): FieldErrors {
    const next: FieldErrors = {};
    if (!name.trim()) next.name = t('validation.nameRequired');
    if (!email.trim()) next.email = t('validation.emailRequired');
    else if (!isValidEmail(email)) next.email = t('validation.emailInvalid');
    if (!password) next.password = t('validation.passwordRequired');
    else if (password.length < MIN_PASSWORD_LENGTH) {
      next.password = t('validation.passwordTooShort', { min: MIN_PASSWORD_LENGTH });
    }
    return next;
  }

  function onSubmit(e: FormEvent) {
    e.preventDefault();
    const next = validate();
    setErrors(next);
    const first = (['name', 'email', 'password'] as const).find((k) => next[k]);
    if (first) {
      ({ name: nameRef, email: emailRef, password: passwordRef })[first].current?.focus();
      return;
    }
    if (agreed) register.mutate();
  }

  const strengthHelp = t('strength.help', {
    level: t(`strength.levels.${strength}`),
    min: MIN_PASSWORD_LENGTH,
  });

  return (
    <AuthLayout>
      <form className="flex flex-col gap-4" onSubmit={onSubmit} noValidate>
        <AuthHeading sub={t('signUp.subtitle')}>{t('signUp.title')}</AuthHeading>
        {invite.data && lockedEmail && (
          <Banner tone="info">
            <Trans
              t={t}
              i18nKey="signUp.joining"
              values={{ org: invite.data.org_name, role: tc(`roles.${invite.data.role}`) }}
              components={{ b: <b className="text-fg" /> }}
            />
          </Banner>
        )}
        <GoogleButton
          label={t('google.signUp')}
          returnTo={inviteToken ? `/invite/${encodeURIComponent(inviteToken)}` : returnTo}
        />
        <OrDivider />
        {serverError && <Banner tone="err">{serverError}</Banner>}
        <Field label={t('fields.name')} error={errors.name}>
          <Input
            ref={nameRef}
            name="name"
            autoComplete="name"
            value={name}
            onChange={(e) => setName(e.target.value)}
          />
        </Field>
        <Field
          label={t('fields.workEmail')}
          error={errors.email}
          help={lockedEmail ? t('signUp.emailLocked') : undefined}
        >
          <Input
            ref={emailRef}
            type="email"
            name="email"
            autoComplete="email"
            inputMode="email"
            value={email}
            readOnly={Boolean(lockedEmail)}
            onChange={(e) => setEmail(e.target.value)}
            onBlur={() => {
              if (email && !isValidEmail(email)) {
                setErrors((x) => ({ ...x, email: t('validation.emailInvalid') }));
              }
            }}
          />
        </Field>
        <Field label={t('fields.password')} error={errors.password} help={strengthHelp}>
          <PasswordInput
            ref={passwordRef}
            name="new-password"
            autoComplete="new-password"
            value={password}
            onChange={(e) => setPassword(e.target.value)}
          />
        </Field>
        <StrengthMeter score={strength} className="-mt-2" />
        <Checkbox
          checked={agreed}
          onCheckedChange={(v) => setAgreed(v === true)}
          label={
            <Trans
              t={t}
              i18nKey="signUp.agree"
              components={{
                terms: <a href="/terms" target="_blank" rel="noopener" className={textLinkClass} />,
                privacy: (
                  <a href="/privacy" target="_blank" rel="noopener" className={textLinkClass} />
                ),
              }}
            />
          }
        />
        <Button
          type="submit"
          variant="primary"
          size="lg"
          className="w-full"
          disabled={!agreed}
          loading={register.isPending}
        >
          {t('signUp.submit')}
        </Button>
        <p className="m-0 text-center text-control text-fg-2">
          {t('signUp.haveAccount')}{' '}
          <Link to="/signin" search={{ return_to: returnTo }} className={textLinkClass}>
            {t('signUp.signIn')}
          </Link>
        </p>
      </form>
    </AuthLayout>
  );
}
