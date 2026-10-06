import { useQuery } from '@tanstack/react-query';
import { Link } from '@tanstack/react-router';
import { useTranslation } from 'react-i18next';
import { usageQuery, usageLevel } from '@/api/usage';
import { useFormat } from '@/i18n/format';
import { cn } from '@/lib/cn';
import { Tooltip } from '@/ui';

const TONE = { ok: 'text-fg-2', warn: 'text-warn', full: 'text-danger' } as const;

/** G.E06 usage meter "62/120 min" → SCR-40 billing; yellow from 80 %, red at 100 %; tooltip = reset date. */
export function UsageMeter() {
  const { t } = useTranslation();
  const fmt = useFormat();
  const { data } = useQuery(usageQuery);
  if (!data) return null;
  const used = data.minutes_used;
  const limit = data.minutes_limit;
  return (
    <Tooltip content={t('nav.usageResets', { date: fmt.date(data.resets_at) })}>
      <Link
        to="/settings/$section"
        params={{ section: 'billing' }}
        aria-label={t('nav.usageLabel', { used, limit })}
        className={cn(
          'rounded-md px-2 py-1 text-control font-semibold tabular-nums focus-ring hover:bg-surface-3',
          TONE[usageLevel(data)],
        )}
      >
        {t('nav.usage', { used, limit })}
      </Link>
    </Tooltip>
  );
}
