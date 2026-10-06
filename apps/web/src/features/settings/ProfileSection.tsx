import { useMutation } from '@tanstack/react-query';
import { useState } from 'react';
import { useTranslation } from 'react-i18next';
import { isApiError } from '@/api/errors';
import { identityApi } from '@/api/identity';
import { useApiErrorMessage } from '@/api/useApiErrorMessage';
import { useAuthStore, useUser } from '@/auth/session';
import { Button, Card, Field, Input, PasswordInput, toast } from '@/ui';

const NAME_MAX = 120;
const PASSWORD_MIN = 10;

/** SCR-40 Profile: P01 name, P03 change password, P04 sign out of other sessions. */
export function ProfileSection() {
  const { t } = useTranslation('settings');
  const user = useUser();
  const [name, setName] = useState<string | null>(null);
  const value = name ?? user?.name ?? '';

  const save = useMutation({
    mutationFn: () => identityApi.updateMe({ name: value.trim() }),
    onSuccess: (me) => {
      useAuthStore.setState((s) => (s.user ? { user: { ...s.user, name: me.name } } : {}));
      setName(null);
      toast({ title: t('profile.saved'), tone: 'success' });
    },
  });
  const saveError = useApiErrorMessage(save.error);

  return (
    <>
      <Card className="flex flex-col gap-4">
        <Field label={t('profile.name')} error={saveError}>
          <Input value={value} maxLength={NAME_MAX} onChange={(e) => setName(e.target.value)} />
        </Field>
        <Field label={t('profile.email')} help={t('profile.emailHelp')}>
          <Input value={user?.email ?? ''} readOnly />
        </Field>
        <div>
          <Button
            variant="primary"
            disabled={!value.trim() || value.trim() === user?.name}
            loading={save.isPending}
            onClick={() => save.mutate()}
          >
            {t('profile.save')}
          </Button>
        </div>
      </Card>
      <PasswordCard />
      <Card className="flex flex-wrap items-center justify-between gap-3">
        <b>{t('profile.sessions')}</b>
        <SignOutOthers />
      </Card>
    </>
  );
}

function PasswordCard() {
  const { t } = useTranslation('settings');
  const [current, setCurrent] = useState('');
  const [next, setNext] = useState('');
  const change = useMutation({
    mutationFn: () => identityApi.changePassword(current, next),
    onSuccess: () => {
      setCurrent('');
      setNext('');
      toast({ title: t('profile.passwordChanged'), tone: 'success' });
    },
  });
  const generic = useApiErrorMessage(change.error);
  const wrong = isApiError(change.error) && change.error.code === 'INVALID_CREDENTIALS';

  return (
    <Card>
      <form
        className="flex flex-col gap-4"
        onSubmit={(e) => {
          e.preventDefault();
          change.mutate();
        }}
      >
        <b>{t('profile.password')}</b>
        <Field
          label={t('profile.currentPassword')}
          error={wrong ? t('profile.wrongPassword') : undefined}
        >
          <PasswordInput
            value={current}
            autoComplete="current-password"
            onChange={(e) => setCurrent(e.target.value)}
          />
        </Field>
        <Field
          label={t('profile.newPassword')}
          help={t('profile.newPasswordHelp')}
          error={change.isError && !wrong ? generic : undefined}
        >
          <PasswordInput
            value={next}
            autoComplete="new-password"
            onChange={(e) => setNext(e.target.value)}
          />
        </Field>
        <div>
          <Button
            type="submit"
            disabled={!current || next.length < PASSWORD_MIN}
            loading={change.isPending}
          >
            {t('profile.changePassword')}
          </Button>
        </div>
      </form>
    </Card>
  );
}

function SignOutOthers() {
  const { t } = useTranslation('settings');
  const revoke = useMutation({
    mutationFn: () => identityApi.signOutOthers(),
    onSuccess: () => toast({ title: t('profile.signedOutOthers'), tone: 'success' }),
  });
  return (
    <Button variant="ghost" loading={revoke.isPending} onClick={() => revoke.mutate()}>
      {t('profile.signOutOthers')}
    </Button>
  );
}
