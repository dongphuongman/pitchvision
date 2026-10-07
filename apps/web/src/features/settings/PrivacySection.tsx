import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { Link, useRouter } from '@tanstack/react-router';
import { Download } from 'lucide-react';
import { useState } from 'react';
import { useTranslation } from 'react-i18next';
import { isApiError } from '@/api/errors';
import { identityApi } from '@/api/identity';
import {
  dataExportDownloadUrl,
  privacyKey,
  privacyQuery,
  requestDataExport,
  type DataExportOut,
} from '@/api/privacy';
import { useApiErrorMessage } from '@/api/useApiErrorMessage';
import { can } from '@/auth/roles';
import { clearSession, signOut, useActiveMembership, useRole, useUser } from '@/auth/session';
import { formatBytes, useFormat } from '@/i18n/format';
import { Banner, Button, Card, Dialog, DialogClose, Field, Input, Spinner, toast } from '@/ui';

/**
 * SCR-40 Data & privacy: D02 export all data (emailed link), D03 delete my account, D04 delete
 * organization (owner) — both through DLG-08 — and D05 links. D01 (training opt-in) waits for PRD Q5.
 */
export function PrivacySection() {
  const { t } = useTranslation('settings');
  const role = useRole();
  const summary = useQuery(privacyQuery);
  return (
    <>
      <DataExportCard latest={summary.data?.data_export ?? null} admin={can(role, 'admin')} />
      <DeleteAccountCard videos={summary.data?.videos} />
      {can(role, 'owner') && (
        <DeleteOrgCard videos={summary.data?.videos} exports={summary.data?.exports} />
      )}
      <p className="m-0 text-sm text-fg-2">
        <Link
          to="/privacy"
          className="rounded-sm font-semibold text-accent focus-ring hover:underline"
        >
          {t('privacy.policy')}
        </Link>
        {' · '}
        <Link
          to="/terms"
          className="rounded-sm font-semibold text-accent focus-ring hover:underline"
        >
          {t('privacy.terms')}
        </Link>
      </p>
    </>
  );
}

function DataExportCard({ latest, admin }: { latest: DataExportOut | null; admin: boolean }) {
  const { t, i18n } = useTranslation('settings');
  const fmt = useFormat();
  const queryClient = useQueryClient();
  const start = useMutation({
    mutationFn: requestDataExport,
    onSuccess: () => toast({ title: t('privacy.exportStarted'), tone: 'success' }),
    onSettled: () => queryClient.invalidateQueries({ queryKey: privacyKey }),
  });
  const error = useApiErrorMessage(start.error);
  const busy = latest?.status === 'queued' || latest?.status === 'running';

  async function download(id: string) {
    try {
      window.location.assign(await dataExportDownloadUrl(id));
    } catch {
      toast({ title: t('privacy.downloadFailed'), tone: 'error' });
      void queryClient.invalidateQueries({ queryKey: privacyKey });
    }
  }

  return (
    <Card className="flex flex-col gap-3">
      <b>{t('privacy.exportTitle')}</b>
      <p className="m-0 text-fg-2">{admin ? t('privacy.exportOrg') : t('privacy.exportAccount')}</p>
      {busy && (
        <p className="m-0 flex items-center gap-2 text-fg-2" role="status">
          <Spinner size={14} /> {t('privacy.exportPreparing')}
        </p>
      )}
      {latest?.status === 'done' && (
        <div className="flex flex-wrap items-center gap-3">
          <span className="text-fg-2">
            {t('privacy.exportReady', {
              size: latest.size_bytes ? formatBytes(i18n.language, latest.size_bytes) : '',
              date: latest.expires_at ? fmt.date(latest.expires_at) : '',
            })}
          </span>
          <Button
            size="sm"
            leadingIcon={<Download size={16} />}
            onClick={() => void download(latest.id)}
          >
            {t('privacy.download')}
          </Button>
        </div>
      )}
      {latest?.status === 'failed' && <Banner tone="err">{t('privacy.exportFailed')}</Banner>}
      {start.isError && <Banner tone="err">{error}</Banner>}
      <div>
        <Button disabled={busy} loading={start.isPending} onClick={() => start.mutate()}>
          {t('privacy.exportButton')}
        </Button>
      </div>
    </Card>
  );
}

/** After a delete: drop the session + every cached query and land on SCR-01. */
function useLeave() {
  const queryClient = useQueryClient();
  const router = useRouter();
  return async (signOutFirst: boolean) => {
    if (signOutFirst) await signOut();
    else clearSession(); // DELETE /me already cleared the refresh cookie
    queryClient.clear();
    await router.navigate({ to: '/' });
  };
}

function DeleteAccountCard({ videos }: { videos: number | undefined }) {
  const { t } = useTranslation('settings');
  const [open, setOpen] = useState(false);
  return (
    <Card className="flex flex-col gap-3">
      <b>{t('privacy.deleteAccountTitle')}</b>
      <p className="m-0 text-fg-2">{t('privacy.deleteAccountBody')}</p>
      <div>
        <Button variant="danger" onClick={() => setOpen(true)}>
          {t('privacy.deleteAccount')}
        </Button>
      </div>
      {open && <DeleteAccountDialog videos={videos} onClose={() => setOpen(false)} />}
    </Card>
  );
}

function DeleteAccountDialog({
  videos,
  onClose,
}: {
  videos: number | undefined;
  onClose: () => void;
}) {
  const { t } = useTranslation('settings');
  const { t: tc } = useTranslation();
  const user = useUser();
  const active = useActiveMembership();
  const leave = useLeave();
  const [typed, setTyped] = useState('');
  const preview = useQuery({
    queryKey: ['deletion-preview'],
    queryFn: ({ signal }) => identityApi.deletionPreview(signal),
    staleTime: 0,
  });
  const del = useMutation({
    mutationFn: () => identityApi.deleteAccount(typed.trim()),
    onSuccess: () => leave(false),
  });
  const error = useApiErrorMessage(del.error);
  const blocking = preview.data?.blocking ?? [];
  const withAccount = preview.data?.deleted_with_account ?? [];
  const matches = !!user && typed.trim().toLowerCase() === user.email;

  return (
    <Dialog
      open
      onOpenChange={(o) => !o && onClose()}
      size="sm"
      title={t('privacy.deleteAccountTitle')}
      footer={
        <>
          <DialogClose asChild>
            <Button variant="ghost">{tc('actions.cancel')}</Button>
          </DialogClose>
          <Button
            variant="danger"
            disabled={!matches || !preview.isSuccess || blocking.length > 0}
            loading={del.isPending}
            onClick={() => del.mutate()}
          >
            {t('privacy.deletePermanently')}
          </Button>
        </>
      }
    >
      <div className="flex flex-col gap-3">
        {preview.isPending && <Spinner size={16} />}
        {blocking.length > 0 ? (
          <Banner tone="warn">
            {t('privacy.blocking', { orgs: blocking.map((o) => o.name).join(', ') })}
          </Banner>
        ) : (
          preview.isSuccess && (
            <>
              <p className="m-0 text-fg-2">{t('privacy.deleteAccountWarn')}</p>
              {withAccount.length > 0 && (
                <div className="text-fg-2">
                  <p className="m-0">{t('privacy.orgsDeletedWithAccount')}</p>
                  <ul className="m-0 mt-1 pl-5">
                    {withAccount.map((o) => (
                      <li key={o.id}>
                        <b className="text-fg">{o.name}</b>
                        {o.id === active?.org_id && videos !== undefined
                          ? ` — ${t('privacy.videoCount', { count: videos })}`
                          : ''}
                      </li>
                    ))}
                  </ul>
                </div>
              )}
              <Field
                label={t('privacy.typeEmail', { email: user?.email ?? '' })}
                error={del.isError ? error : undefined}
              >
                <Input
                  value={typed}
                  autoComplete="off"
                  onChange={(e) => setTyped(e.target.value)}
                />
              </Field>
            </>
          )
        )}
      </div>
    </Dialog>
  );
}

function DeleteOrgCard({ videos, exports }: { videos?: number; exports?: number }) {
  const { t } = useTranslation('settings');
  const { t: tc } = useTranslation();
  const active = useActiveMembership();
  const leave = useLeave();
  const [open, setOpen] = useState(false);
  const [typed, setTyped] = useState('');
  const del = useMutation({
    mutationFn: () => {
      if (!active) throw new Error('No active organization');
      return identityApi.deleteOrg(active.org_id, typed.trim());
    },
    onSuccess: () => leave(true),
  });
  const error = useApiErrorMessage(del.error);
  if (!active) return null;
  const wrong = isApiError(del.error) && del.error.code === 'VALIDATION_FAILED';

  return (
    <Card className="flex flex-col gap-3">
      <b>{t('privacy.deleteOrgTitle')}</b>
      <p className="m-0 text-fg-2">{t('privacy.deleteOrgBody', { name: active.org_name })}</p>
      <div>
        <Button variant="danger" onClick={() => setOpen(true)}>
          {t('privacy.deleteOrg')}
        </Button>
      </div>
      <Dialog
        open={open}
        onOpenChange={(o) => {
          setOpen(o);
          if (!o) setTyped('');
        }}
        size="sm"
        title={t('privacy.deleteOrgConfirmTitle', { name: active.org_name })}
        footer={
          <>
            <DialogClose asChild>
              <Button variant="ghost">{tc('actions.cancel')}</Button>
            </DialogClose>
            <Button
              variant="danger"
              disabled={typed.trim() !== active.org_name}
              loading={del.isPending}
              onClick={() => del.mutate()}
            >
              {t('privacy.deletePermanently')}
            </Button>
          </>
        }
      >
        <div className="flex flex-col gap-3">
          <p className="m-0 text-fg-2">
            {videos !== undefined && exports !== undefined
              ? t('privacy.deleteOrgCounts', { videos, exports })
              : t('privacy.deleteOrgWarn')}
          </p>
          <Field
            label={t('privacy.typeOrgName', { name: active.org_name })}
            error={del.isError ? (wrong ? t('privacy.nameMismatch') : error) : undefined}
          >
            <Input value={typed} autoComplete="off" onChange={(e) => setTyped(e.target.value)} />
          </Field>
        </div>
      </Dialog>
    </Card>
  );
}
