import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { useState } from 'react';
import { useTranslation } from 'react-i18next';
import { isApiError } from '@/api/errors';
import { identityApi, type InviteRole, type Member, type Role } from '@/api/identity';
import { useApiErrorMessage } from '@/api/useApiErrorMessage';
import { can } from '@/auth/roles';
import { useActiveMembership, useUser } from '@/auth/session';
import { useFormat } from '@/i18n/format';
import { parseEmails } from './model';
import {
  Banner,
  Button,
  Card,
  Dialog,
  DialogClose,
  Field,
  Select,
  Skeleton,
  Textarea,
  toast,
} from '@/ui';

const INVITE_ROLES: readonly InviteRole[] = ['viewer', 'editor', 'admin'];
const ALL_ROLES: readonly Role[] = ['viewer', 'editor', 'admin', 'owner'];
const MAX_INVITES = 50;

/**
 * SCR-40 Members (admin+): M01 invite, M02 role per member (only owners grant / change owner —
 * the API enforces it and keeps one owner), M03 remove, M04 pending invites resend / revoke.
 */
export function MembersSection() {
  const { t } = useTranslation('settings');
  const { t: tc } = useTranslation();
  const fmt = useFormat();
  const active = useActiveMembership();
  const me = useUser();
  const qc = useQueryClient();
  const orgId = active?.org_id ?? '';
  const isOwner = can(active?.role ?? null, 'owner');
  const membersKey = ['identity', 'members', orgId] as const;
  const invitesKey = ['identity', 'invites', orgId] as const;
  const members = useQuery({
    queryKey: membersKey,
    queryFn: ({ signal }) => identityApi.members(orgId, signal),
    enabled: !!orgId,
  });
  const invites = useQuery({
    queryKey: invitesKey,
    queryFn: ({ signal }) => identityApi.invites(orgId, signal),
    enabled: !!orgId,
  });
  const refresh = () => {
    void qc.invalidateQueries({ queryKey: membersKey });
    void qc.invalidateQueries({ queryKey: invitesKey });
  };

  const [toRemove, setToRemove] = useState<Member | null>(null);
  const setRole = useMutation({
    mutationFn: ({ userId, role }: { userId: string; role: Role }) =>
      identityApi.setMemberRole(orgId, userId, role),
    onSuccess: () => toast({ title: t('members.roleChanged'), tone: 'success' }),
    onError: (e) =>
      toast({
        title:
          isApiError(e) && e.code === 'LAST_OWNER' ? t('members.lastOwner') : tc('error.title'),
        tone: 'error',
      }),
    onSettled: refresh,
  });
  const remove = useMutation({
    mutationFn: (userId: string) => identityApi.removeMember(orgId, userId),
    onSuccess: () => toast({ title: t('members.removed'), tone: 'success' }),
    onError: (e) =>
      toast({
        title:
          isApiError(e) && e.code === 'LAST_OWNER' ? t('members.lastOwner') : tc('error.title'),
        tone: 'error',
      }),
    onSettled: refresh,
  });
  const resend = useMutation({
    mutationFn: (id: string) => identityApi.resendInvite(orgId, id),
    onSuccess: () => toast({ title: t('members.resent'), tone: 'success' }),
    onSettled: refresh,
  });
  const revoke = useMutation({
    mutationFn: (id: string) => identityApi.revokeInvite(orgId, id),
    onSuccess: () => toast({ title: t('members.revoked'), tone: 'success' }),
    onSettled: refresh,
  });

  if (!active) return null;
  return (
    <>
      <InviteCard orgId={orgId} onSent={refresh} />
      <Card className="flex flex-col gap-3">
        <b>{t('members.people')}</b>
        {members.isPending ? (
          <Skeleton className="h-24" />
        ) : (
          <ul className="m-0 flex list-none flex-col divide-y divide-line p-0">
            {(members.data ?? []).map((m) => {
              const self = m.user_id === me?.id;
              // Only owners may change / remove an owner or grant owner (API: 403 otherwise).
              const locked = m.role === 'owner' && !isOwner;
              return (
                <li key={m.user_id} className="flex flex-wrap items-center gap-3 py-2.5">
                  <div className="flex min-w-0 flex-1 flex-col">
                    <span className="truncate font-semibold">
                      {m.name}
                      {self && <span className="font-normal text-fg-3"> ({t('members.you')})</span>}
                    </span>
                    <span className="truncate text-caption text-fg-3">{m.email}</span>
                  </div>
                  <div className="w-36">
                    <Select
                      aria-label={t('members.roleFor', { name: m.name })}
                      value={m.role}
                      disabled={locked || setRole.isPending}
                      onValueChange={(role) =>
                        setRole.mutate({ userId: m.user_id, role: role as Role })
                      }
                      options={(isOwner ? ALL_ROLES : INVITE_ROLES).map((r) => ({
                        value: r,
                        label: tc(`roles.${r}`),
                      }))}
                    />
                  </div>
                  <Button
                    variant="ghost"
                    size="sm"
                    disabled={locked || self}
                    onClick={() => setToRemove(m)}
                  >
                    {t('members.remove')}
                  </Button>
                </li>
              );
            })}
          </ul>
        )}
      </Card>
      <Card className="flex flex-col gap-3">
        <b>{t('members.pending')}</b>
        {invites.isPending ? (
          <Skeleton className="h-12" />
        ) : !invites.data?.length ? (
          <p className="m-0 text-control text-fg-3">{t('members.noPending')}</p>
        ) : (
          <ul className="m-0 flex list-none flex-col divide-y divide-line p-0">
            {invites.data.map((inv) => {
              const expired = new Date(inv.expires_at).getTime() < invites.dataUpdatedAt;
              return (
                <li key={inv.id} className="flex flex-wrap items-center gap-3 py-2.5">
                  <div className="flex min-w-0 flex-1 flex-col">
                    <span className="truncate font-semibold">{inv.email}</span>
                    <span className="text-caption text-fg-3">
                      {tc(`roles.${inv.role}`)} ·{' '}
                      {expired
                        ? t('members.expired')
                        : t('members.expires', { date: fmt.date(inv.expires_at) })}
                    </span>
                  </div>
                  <Button size="sm" variant="ghost" onClick={() => resend.mutate(inv.id)}>
                    {t('members.resend')}
                  </Button>
                  <Button size="sm" variant="ghost" onClick={() => revoke.mutate(inv.id)}>
                    {t('members.revoke')}
                  </Button>
                </li>
              );
            })}
          </ul>
        )}
      </Card>
      <Dialog
        open={toRemove !== null}
        onOpenChange={(o) => !o && setToRemove(null)}
        size="sm"
        title={t('members.removeTitle', { name: toRemove?.name ?? '' })}
        footer={
          <>
            <DialogClose asChild>
              <Button variant="ghost">{tc('actions.cancel')}</Button>
            </DialogClose>
            <Button
              variant="danger"
              onClick={() => {
                if (toRemove) remove.mutate(toRemove.user_id);
                setToRemove(null);
              }}
            >
              {t('members.remove')}
            </Button>
          </>
        }
      >
        <p className="m-0 text-fg-2">{t('members.removeBody', { name: toRemove?.name ?? '' })}</p>
      </Dialog>
    </>
  );
}

function InviteCard({ orgId, onSent }: { orgId: string; onSent: () => void }) {
  const { t } = useTranslation('settings');
  const { t: tc } = useTranslation();
  const [text, setText] = useState('');
  const [role, setRole] = useState<InviteRole>('editor');
  const emails = parseEmails(text);
  const send = useMutation({
    mutationFn: () => identityApi.invite(orgId, emails, role),
    onSuccess: (res) => {
      setText('');
      onSent();
      toast({ title: t('members.sent', { count: res.length }), tone: 'success' });
    },
  });
  const error = useApiErrorMessage(send.error);

  return (
    <Card>
      <form
        className="flex flex-col gap-3"
        onSubmit={(e) => {
          e.preventDefault();
          send.mutate();
        }}
      >
        <b>{t('members.invite')}</b>
        <Field label={t('members.emails')} help={t('members.emailsHelp')}>
          <Textarea rows={2} value={text} onChange={(e) => setText(e.target.value)} />
        </Field>
        <div className="flex flex-wrap items-end gap-3">
          <Field label={t('members.role')} className="w-40">
            <Select
              value={role}
              onValueChange={(v) => setRole(v as InviteRole)}
              options={INVITE_ROLES.map((r) => ({ value: r, label: tc(`roles.${r}`) }))}
            />
          </Field>
          <Button
            type="submit"
            variant="primary"
            disabled={emails.length === 0 || emails.length > MAX_INVITES}
            loading={send.isPending}
          >
            {t('members.send')}
          </Button>
        </div>
        {error && <Banner tone="err">{error}</Banner>}
      </form>
    </Card>
  );
}
