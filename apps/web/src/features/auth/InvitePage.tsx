import { useMutation, useQuery } from '@tanstack/react-query';
import { getRouteApi, Link, useNavigate } from '@tanstack/react-router';
import { CalendarX2, Link2Off } from 'lucide-react';
import type { ReactNode } from 'react';
import { Trans, useTranslation } from 'react-i18next';
import { isApiError } from '@/api/errors';
import { identityApi, type InvitePreview } from '@/api/identity';
import { useApiErrorMessage } from '@/api/useApiErrorMessage';
import { applySession, signOut, useAuthStore } from '@/auth/session';
import { useFocusOnChange } from '@/lib/useFocusOnChange';
import { Banner, Button, Spinner, toast } from '@/ui';
import { CenteredCard, FeatureIcon, textLinkClass } from './AuthLayout';

const route = getRouteApi('/invite/$token');

type InviteState = 'loading' | 'invalid' | 'expired' | 'signedOut' | 'match' | 'mismatch' | 'error';

function initials(name: string): string {
  return (
    name
      .split(/\s+/)
      .filter(Boolean)
      .slice(0, 2)
      .map((p) => p[0]?.toUpperCase() ?? '')
      .join('') || '?'
  );
}

/**
 * SCR-52 Accept invitation (`/invite/:token`, public). States: not signed in → sign up with the
 * email locked; signed in with the invited email → accept; another email → message + "Sign in
 * with another account"; expired / invalid.
 */
export function InvitePage() {
  const { t } = useTranslation('auth');
  const { t: tc } = useTranslation();
  const navigate = useNavigate();
  const { token } = route.useParams();
  const status = useAuthStore((s) => s.status);
  const userEmail = useAuthStore((s) => s.user?.email);

  const preview = useQuery({
    queryKey: ['identity', 'invite', token],
    queryFn: ({ signal }) => identityApi.getInvite(token, signal),
    retry: false,
  });

  const accept = useMutation({
    mutationFn: () => identityApi.acceptInvite(token),
    onSuccess: (session) => {
      applySession(session);
      toast({ title: t('invite.joined', { org: preview.data?.org_name ?? '' }), tone: 'success' });
      void navigate({ to: '/library', replace: true });
    },
  });
  const acceptCode = isApiError(accept.error) ? accept.error.code : undefined;
  const acceptError = useApiErrorMessage(
    acceptCode === 'INVITE_EMAIL_MISMATCH' || acceptCode === 'TOKEN_EXPIRED' ? null : accept.error,
  );

  const state = resolveState();
  const headingRef = useFocusOnChange<HTMLHeadingElement>(state);
  const invitePath = `/invite/${encodeURIComponent(token)}`;

  function resolveState(): InviteState {
    if (preview.isPending) return 'loading';
    if (preview.isError) {
      const code = isApiError(preview.error) ? preview.error.code : undefined;
      if (code === 'NOT_FOUND') return 'invalid';
      if (code === 'TOKEN_EXPIRED') return 'expired';
      return 'error';
    }
    if (preview.data.expired || acceptCode === 'TOKEN_EXPIRED') return 'expired';
    if (status !== 'authenticated') return 'signedOut';
    if (acceptCode === 'INVITE_EMAIL_MISMATCH') return 'mismatch';
    return userEmail?.toLowerCase() === preview.data.email.toLowerCase() ? 'match' : 'mismatch';
  }

  async function signInWithAnotherAccount() {
    // Sign out but keep the invite: come back here after signing in.
    await signOut();
    void navigate({ to: '/signin', search: { return_to: invitePath } });
  }

  if (state === 'loading') {
    return (
      <CenteredCard className="max-w-[460px] items-center py-12">
        <Spinner size={24} />
      </CenteredCard>
    );
  }

  if (state === 'invalid' || state === 'expired' || state === 'error') {
    const expired = state === 'expired';
    return (
      <CenteredCard className="max-w-[460px] text-center">
        <FeatureIcon className="mx-auto mt-4 bg-[color-mix(in_srgb,var(--warn)_14%,transparent)] text-warn">
          {expired ? <CalendarX2 size={24} /> : <Link2Off size={24} />}
        </FeatureIcon>
        <h1 ref={headingRef} tabIndex={-1} className="text-[22px] outline-none">
          {expired ? t('invite.expiredTitle') : t('invite.invalidTitle')}
        </h1>
        <p className="m-0 text-fg-2">
          {expired
            ? preview.data?.inviter_name
              ? t('invite.expiredBody', { inviter: preview.data.inviter_name })
              : t('invite.expiredBodyNoInviter')
            : state === 'error'
              ? tc('error.body')
              : t('invite.invalidBody')}
        </p>
        <Button asChild variant="primary" size="lg" className="mt-2 w-full">
          <Link to="/">{t('invite.goHome')}</Link>
        </Button>
      </CenteredCard>
    );
  }

  const invite: InvitePreview = preview.data as InvitePreview;
  let body: ReactNode;
  if (state === 'signedOut') {
    body = (
      <>
        <Button asChild variant="primary" size="lg" className="mt-2.5 w-full">
          <Link to="/signup" search={{ invite: token }}>
            {t('invite.accept')}
          </Link>
        </Button>
        <p className="m-0 mt-2 text-control text-fg-2">
          {t('invite.haveAccount')}{' '}
          <Link
            to="/signin"
            search={{ return_to: invitePath, email: invite.email }}
            className={textLinkClass}
          >
            {t('invite.signIn')}
          </Link>
        </p>
      </>
    );
  } else if (state === 'match') {
    body = (
      <>
        {acceptError && <Banner tone="err">{acceptError}</Banner>}
        <Button
          variant="primary"
          size="lg"
          className="mt-2.5 w-full"
          loading={accept.isPending}
          onClick={() => accept.mutate()}
        >
          {t('invite.accept')}
        </Button>
        <p className="m-0 mt-2 text-control text-fg-2">
          {t('invite.notYou', { email: invite.email })}{' '}
          <button
            type="button"
            className={textLinkClass}
            onClick={() => void signInWithAnotherAccount()}
          >
            {t('invite.otherAccount')}
          </button>
        </p>
      </>
    );
  } else {
    body = (
      <>
        <Banner tone="warn" className="text-left">
          <Trans
            t={t}
            i18nKey="invite.mismatch"
            values={{ invited: invite.email, current: userEmail ?? '' }}
            components={{ b: <b className="text-fg" /> }}
          />
        </Banner>
        <Button
          variant="primary"
          size="lg"
          className="mt-2.5 w-full"
          onClick={() => void signInWithAnotherAccount()}
        >
          {t('invite.otherAccount')}
        </Button>
      </>
    );
  }

  return (
    <CenteredCard className="max-w-[460px] text-center">
      <div
        aria-hidden="true"
        className="mx-auto mt-4 grid size-14 place-items-center rounded-full bg-[linear-gradient(135deg,#4da3ff,#7c5cff)] text-lg font-bold text-white"
      >
        {initials(invite.inviter_name ?? invite.org_name)}
      </div>
      <h1 ref={headingRef} tabIndex={-1} className="text-[22px] outline-none">
        {invite.inviter_name
          ? t('invite.title', { inviter: invite.inviter_name, org: invite.org_name })
          : t('invite.titleNoInviter', { org: invite.org_name })}
      </h1>
      <p className="-mt-2 text-fg-2">
        <Trans
          t={t}
          i18nKey="invite.as"
          values={{ role: tc(`roles.${invite.role}`), region: tc(`regions.${invite.region}`) }}
          components={{ b: <b className="text-fg" /> }}
        />
      </p>
      {body}
    </CenteredCard>
  );
}
