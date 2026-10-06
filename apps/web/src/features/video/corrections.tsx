import { Link, useNavigate } from '@tanstack/react-router';
import { ArrowLeft } from 'lucide-react';
import type { ReactNode } from 'react';
import { useTranslation } from 'react-i18next';
import { problemJobId, problemReason } from '@/api/corrections';
import { useApiErrorMessage } from '@/api/useApiErrorMessage';
import { Banner, Button } from '@/ui';

/**
 * "← Back" (E01) to the screen that opened SCR-14 / SCR-15: `?return_to=` (same-origin,
 * validated by the router) or the workspace.
 */
export function BackLink({ href }: { href: string }) {
  const { t } = useTranslation('video');
  const navigate = useNavigate();
  return (
    <a
      href={href}
      onClick={(e) => {
        e.preventDefault();
        void navigate({ href });
      }}
      className="inline-flex items-center gap-1.5 rounded-xs text-sm font-semibold text-fg-2 focus-ring hover:text-fg"
    >
      <ArrowLeft size={16} aria-hidden="true" />
      {t('corrections.back')}
    </a>
  );
}

/**
 * Error of a correction request: 409 "a job is running" (with a link to SCR-13), 409
 * `no_analysis`, or a reason-specific message from `reasons`, else the localised error code.
 */
export function CorrectionError({
  error,
  videoId,
  reasons,
}: {
  error: unknown;
  videoId: string;
  reasons?: (reason: string) => ReactNode | undefined;
}) {
  const { t } = useTranslation('video');
  const generic = useApiErrorMessage(error);
  const reason = problemReason(error);
  const jobId = problemJobId(error);
  const message = jobId
    ? t('corrections.busy')
    : reason === 'no_analysis'
      ? t('corrections.noAnalysis')
      : ((reason ? reasons?.(reason) : undefined) ?? generic);
  return (
    <Banner
      tone="err"
      actions={
        jobId ? (
          <Button asChild size="sm">
            <Link to="/videos/$videoId/processing" params={{ videoId }}>
              {t('corrections.openProcessing')}
            </Link>
          </Button>
        ) : null
      }
    >
      {message}
    </Banner>
  );
}
