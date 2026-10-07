import { useQuery } from '@tanstack/react-query';
import { Link } from '@tanstack/react-router';
import { useTranslation } from 'react-i18next';
import { usageQuery, usageLevel } from '@/api/usage';
import { can } from '@/auth/roles';
import { useRole } from '@/auth/session';
import { useFormat } from '@/i18n/format';
import { cn } from '@/lib/cn';
import { Tooltip } from '@/ui';
import { useOpenUpgrade } from './useUpgrade';

const TONE = { ok: 'text-fg-2', warn: 'text-warn', full: 'text-danger' } as const;

/** G.E06 usage meter "62/120 min" → SCR-40 billing (owner) / SCR-41 (others); yellow from 80 %, red at 100 %; tooltip = reset date. */
export function UsageMeter() {
  const { t } = useTranslation();
  const fmt = useFormat();
  const { data } = useQuery(usageQuery);
  const isOwner = can(useRole(), 'owner');
  const openUpgrade = useOpenUpgrade();
  if (!data) return null;
  const used = data.minutes_used;
  const limit = data.minutes_limit;
  const className = cn(
    'rounded-md px-2 py-1 text-control font-semibold whitespace-nowrap tabular-nums focus-ring hover:bg-surface-3',
    TONE[usageLevel(data)],
  );
  const label = t('nav.usageLabel', { used, limit });
  return (
    <Tooltip content={t('nav.usageResets', { date: fmt.date(data.resets_at) })}>
      {/* Billing is owner-only; everyone else sees the plans (SCR-41) and can notify the owner. */}
      {isOwner ? (
        <Link
          to="/settings/$section"
          params={{ section: 'billing' }}
          aria-label={label}
          className={className}
        >
          {t('nav.usage', { used, limit })}
        </Link>
      ) : (
        <button
          type="button"
          aria-label={label}
          className={className}
          onClick={() => openUpgrade('quota')}
        >
          {t('nav.usage', { used, limit })}
        </button>
      )}
    </Tooltip>
  );
}
