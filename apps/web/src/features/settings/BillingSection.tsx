import { useQuery } from '@tanstack/react-query';
import { useTranslation } from 'react-i18next';
import { usageLevel, usageQuery } from '@/api/usage';
import { useOpenUpgrade } from '@/app/useUpgrade';
import { useFormat } from '@/i18n/format';
import { Badge, Button, Card, Progress, Skeleton } from '@/ui';

const TONE = { ok: 'accent', warn: 'warn', full: 'danger' } as const;

function planOf(plan: string): 'free' | 'pro' | 'team' {
  return plan === 'pro' || plan === 'team' ? plan : 'free';
}

/** SCR-40 Billing & usage (owner): B01 plan card + Change plan (SCR-41), this month's minutes. */
export function BillingSection() {
  const { t } = useTranslation('settings');
  const fmt = useFormat();
  const openUpgrade = useOpenUpgrade();
  const { data } = useQuery(usageQuery);
  if (!data) return <Skeleton className="h-40" />;
  const used = data.minutes_used;
  const limit = data.minutes_limit;

  return (
    <>
      <Card className="flex flex-wrap items-center gap-4">
        <div className="flex min-w-0 flex-1 flex-col gap-1">
          <span className="text-caption font-semibold text-fg-2">{t('billing.plan')}</span>
          <span className="flex items-center gap-2 text-lg font-bold">
            {t(`upgrade.plans.${planOf(data.plan)}.name`)}
            <Badge tone="ok">{t('upgrade.current')}</Badge>
          </span>
          <span className="text-control text-fg-2">
            {t('billing.exports', { height: data.export_max_height })},{' '}
            {data.export_watermark ? t('billing.watermark') : t('billing.noWatermark')}
          </span>
        </div>
        <Button variant="primary" onClick={() => openUpgrade()}>
          {t('billing.changePlan')}
        </Button>
      </Card>
      <Card className="flex flex-col gap-2">
        <div className="flex items-baseline justify-between gap-3">
          <b>{t('billing.usage')}</b>
          <span className="text-control tabular-nums">
            {t('billing.usageValue', { used, limit })}
          </span>
        </div>
        <Progress
          value={limit > 0 ? Math.min(100, (100 * used) / limit) : 100}
          label={t('billing.usage')}
          tone={TONE[usageLevel(data)]}
        />
        <span className="text-caption text-fg-3">
          {t('billing.resets', { date: fmt.date(data.resets_at) })}
        </span>
      </Card>
      <p className="m-0 text-caption text-fg-3">{t('billing.payments')}</p>
    </>
  );
}
