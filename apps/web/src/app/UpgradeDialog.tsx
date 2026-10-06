import { useMutation, useQuery } from '@tanstack/react-query';
import { Check } from 'lucide-react';
import { useState } from 'react';
import { useTranslation } from 'react-i18next';
import { identityApi } from '@/api/identity';
import { usageQuery } from '@/api/usage';
import { useApiErrorMessage } from '@/api/useApiErrorMessage';
import { can } from '@/auth/roles';
import { useActiveMembership } from '@/auth/session';
import { cn } from '@/lib/cn';
import type { UpgradeReason } from '@/router';
import { Badge, Banner, Button, Dialog, SegmentedControl, toast } from '@/ui';

type Period = 'monthly' | 'annual';
type PlanId = 'free' | 'pro' | 'team';

const PLANS: readonly PlanId[] = ['free', 'pro', 'team'];
const PRICING_URL = '/#pricing';

/**
 * SCR-41 Upgrade (modal lg). Billing is not live in M1: "Choose Pro" / "Contact sales" join the
 * waitlist (POST /orgs/{id}/upgrade-requests); a non-owner gets "Notify owner" (E07), which
 * emails the owners. The banner explains what was blocked (`reason`).
 */
export function UpgradeDialog({
  open,
  reason,
  onOpenChange,
}: {
  open: boolean;
  reason?: UpgradeReason;
  onOpenChange: (open: boolean) => void;
}) {
  const { t } = useTranslation('settings');
  const active = useActiveMembership();
  const isOwner = can(active?.role ?? null, 'owner');
  const usage = useQuery({ ...usageQuery, enabled: open });
  const current = (usage.data?.plan ?? 'free') as PlanId;
  const [period, setPeriod] = useState<Period>('monthly');
  const [done, setDone] = useState<PlanId | null>(null);

  const request = useMutation({
    mutationFn: (plan: 'pro' | 'team') => {
      if (!active) throw new Error('No active organization');
      return identityApi.requestUpgrade(active.org_id, { plan, period, reason: reason ?? null });
    },
    onSuccess: (res) => {
      if (isOwner) setDone(res.plan);
      else toast({ title: t('upgrade.ownerNotified'), tone: 'success' });
    },
  });
  const error = useApiErrorMessage(request.error);

  const banner =
    reason === 'quota' && usage.data
      ? t('upgrade.reason.quota', {
          used: usage.data.minutes_used,
          limit: usage.data.minutes_limit,
        })
      : reason
        ? t(`upgrade.reason.${reason}`)
        : null;

  return (
    <Dialog
      open={open}
      onOpenChange={(o) => {
        if (!o) setDone(null);
        onOpenChange(o);
      }}
      size="lg"
      title={done ? t('upgrade.waitlistTitle') : t('upgrade.title')}
      footer={
        <>
          <a
            href={PRICING_URL}
            target="_blank"
            rel="noopener noreferrer"
            className="mr-auto rounded-xs text-sm font-semibold text-accent focus-ring hover:underline"
          >
            {t('upgrade.compare')}
          </a>
          <Button variant="ghost" onClick={() => onOpenChange(false)}>
            {t('upgrade.later')}
          </Button>
        </>
      }
    >
      {done ? (
        <Banner tone="info">
          {t('upgrade.waitlist', { plan: t(`upgrade.plans.${done}.name`) })}
        </Banner>
      ) : (
        <div className="flex flex-col gap-4">
          {banner && <Banner tone="info">{banner}</Banner>}
          {!isOwner && (
            <Banner
              tone="warn"
              actions={
                <Button size="sm" loading={request.isPending} onClick={() => request.mutate('pro')}>
                  {t('upgrade.notifyOwner')}
                </Button>
              }
            >
              {t('upgrade.notOwner')}
            </Banner>
          )}
          <SegmentedControl<Period>
            aria-label={t('upgrade.period')}
            value={period}
            onValueChange={setPeriod}
            options={[
              { value: 'monthly', label: t('upgrade.monthly') },
              { value: 'annual', label: t('upgrade.annual') },
            ]}
          />
          <ul className="m-0 grid list-none gap-3 p-0 md:grid-cols-3">
            {PLANS.map((p) => (
              <li
                key={p}
                aria-label={t(`upgrade.plans.${p}.name`)}
                className={cn(
                  'flex flex-col gap-2 rounded-lg border border-line bg-surface-2 p-4',
                  p === 'pro' && 'border-accent',
                )}
              >
                <div className="flex items-center justify-between">
                  <b className="text-md">{t(`upgrade.plans.${p}.name`)}</b>
                  {p === current && <Badge tone="ok">{t('upgrade.current')}</Badge>}
                </div>
                <span className="text-lg font-bold">{t(`upgrade.plans.${p}.minutes`)}</span>
                <ul className="m-0 flex list-none flex-col gap-1 p-0 text-control text-fg-2">
                  {(['f1', 'f2'] as const).map((f) => (
                    <li key={f} className="flex items-center gap-1.5">
                      <Check size={14} className="text-accent" aria-hidden="true" />
                      {t(`upgrade.plans.${p}.${f}`)}
                    </li>
                  ))}
                </ul>
                <div className="mt-auto pt-2">
                  {p === 'pro' && isOwner && current !== 'pro' && (
                    <Button
                      variant="primary"
                      className="w-full"
                      loading={request.isPending && request.variables === 'pro'}
                      onClick={() => request.mutate('pro')}
                    >
                      {t('upgrade.choosePro')}
                    </Button>
                  )}
                  {p === 'team' && isOwner && current !== 'team' && (
                    <Button
                      className="w-full"
                      loading={request.isPending && request.variables === 'team'}
                      onClick={() => request.mutate('team')}
                    >
                      {t('upgrade.contactSales')}
                    </Button>
                  )}
                </div>
              </li>
            ))}
          </ul>
          {error && <Banner tone="err">{error}</Banner>}
        </div>
      )}
    </Dialog>
  );
}
