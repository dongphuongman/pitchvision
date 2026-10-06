import { useQuery, useQueryClient } from '@tanstack/react-query';
import { Link, getRouteApi, useNavigate } from '@tanstack/react-router';
import { Check, Circle, Loader2 } from 'lucide-react';
import { useEffect, useState } from 'react';
import { useTranslation } from 'react-i18next';
import { Page, PageHeader } from '@/app/Page';
import {
  canRetry,
  cancelJob,
  retryJob,
  toLatestJob,
  type JobStage,
  type LiveJob,
} from '@/api/jobs';
import { useApiErrorMessage } from '@/api/useApiErrorMessage';
import { useJobProgress } from '@/api/useJobProgress';
import { createJob } from '@/api/uploads';
import { patchVideoInCache, videoQuery } from '@/api/videos';
import { can } from '@/auth/roles';
import { useRole } from '@/auth/session';
import { formatTimecode } from '@/i18n/format';
import { Badge, Banner, Button, Card, JOB_STATUS_TONE, Progress, Skeleton } from '@/ui';

const route = getRouteApi('/_app/videos/$videoId/processing');

/** UIUX SCR-13 steps; each pipeline stage (pipeline-stages.md) belongs to one step. */
const STEPS = ['upload', 'prepare', 'teams', 'analyze', 'finalize'] as const;
type Step = (typeof STEPS)[number];
const STEP_OF: Record<JobStage, Step> = {
  ingest: 'upload',
  normalize: 'prepare',
  shots: 'prepare',
  plan: 'prepare',
  calibrate: 'teams',
  infer: 'analyze',
  stitch: 'finalize',
  tiles: 'finalize',
  load_db: 'finalize',
};
const MAX_SEGMENT_BLOCKS = 60;

function currentStep(job: LiveJob): number {
  if (job.status === 'done') return STEPS.length;
  if (job.stage && job.stage in STEP_OF) return STEPS.indexOf(STEP_OF[job.stage as JobStage]);
  return job.status === 'queued' ? 1 : 0; // source already stored; preparing comes next
}

/** SCR-13 Processing — live over WS topic `job:{id}` (M1-FE-13). */
export function ProcessingPage() {
  const { videoId } = route.useParams();
  const { t } = useTranslation('video');
  const { t: tc } = useTranslation();
  const navigate = useNavigate();
  const qc = useQueryClient();
  const canEdit = can(useRole(), 'editor');
  const video = useQuery(videoQuery(videoId));
  const { job } = useJobProgress(video.data ?? { id: videoId, latest_job: null }, {
    enabled: video.isSuccess,
  });
  const [confirmCancel, setConfirmCancel] = useState(false);
  const [busy, setBusy] = useState(false);
  const [actionError, setActionError] = useState<unknown>(null);
  const actionMessage = useApiErrorMessage(actionError);

  // A01: done → the workspace (SCR-14 team confirmation / SCR-15 calibration land in W5–W6).
  useEffect(() => {
    if (job?.status === 'done') {
      void navigate({
        to: '/videos/$videoId/$tab',
        params: { videoId, tab: 'overview' },
        replace: true,
      });
    }
  }, [job?.status, navigate, videoId]);

  const run = async (fn: () => Promise<LiveJob | null>) => {
    setBusy(true);
    setActionError(null);
    try {
      const next = await fn();
      if (next) patchVideoInCache(qc, videoId, { latest_job: next });
    } catch (e) {
      setActionError(e);
    } finally {
      setBusy(false);
      setConfirmCancel(false);
    }
  };

  if (video.isPending) {
    return (
      <Page>
        <Skeleton className="h-40" />
      </Page>
    );
  }
  if (video.isError) {
    return (
      <Page>
        <Banner tone="err">
          {tc('errors.generic', { defaultValue: 'Something went wrong.' })}
        </Banner>
      </Page>
    );
  }

  const v = video.data;
  const step = job ? currentStep(job) : -1;
  const pct = Math.round(job?.progress ?? 0);
  const segs = job?.segments;
  const failed = job?.status === 'failed';
  const cancelled = job?.status === 'cancelled';
  const running = job !== null && !failed && !cancelled && job.status !== 'done';

  return (
    <Page>
      <PageHeader
        title={v.title ?? t('processing.title')}
        meta={<Link to="/library">{t('processing.backToLibrary')}</Link>}
        actions={
          job ? (
            <Badge tone={JOB_STATUS_TONE[job.status]}>{tc(`status.job.${job.status}`)}</Badge>
          ) : null
        }
      />
      <Card className="flex flex-col gap-5">
        <ol
          className="m-0 flex list-none flex-wrap items-center gap-2 p-0"
          aria-label={t('processing.steps')}
        >
          {STEPS.map((s, i) => {
            const state = i < step ? 'done' : i === step && running ? 'current' : 'todo';
            return (
              <li
                key={s}
                className="text-body-sm flex items-center gap-2"
                aria-current={state === 'current' ? 'step' : undefined}
              >
                {state === 'done' ? (
                  <Check size={16} className="text-accent" aria-hidden="true" />
                ) : state === 'current' ? (
                  <Loader2 size={16} className="animate-spin text-info" aria-hidden="true" />
                ) : (
                  <Circle size={16} className="text-fg-3" aria-hidden="true" />
                )}
                <span className={state === 'todo' ? 'text-fg-3' : 'text-fg'}>
                  {t(`processing.step.${s}`, {
                    context: v.source_type === 'youtube' && s === 'upload' ? 'youtube' : undefined,
                  })}
                </span>
                {i < STEPS.length - 1 && (
                  <span aria-hidden="true" className="text-fg-3">
                    ──
                  </span>
                )}
              </li>
            );
          })}
        </ol>

        {job === null && (
          <Banner
            tone="info"
            actions={
              canEdit && v.status === 'ready' ? (
                <Button
                  variant="primary"
                  disabled={busy}
                  onClick={() => void run(async () => toLatestJob(await createJob(videoId)))}
                >
                  {t('processing.start')}
                </Button>
              ) : null
            }
          >
            {v.status === 'ready' ? t('processing.notAnalyzed') : t('processing.waitingSource')}
          </Banner>
        )}

        {job && !failed && !cancelled && (
          <>
            <Progress
              value={job.status === 'queued' ? null : pct}
              label={t('processing.progress')}
            />
            <p className="text-body-sm m-0 text-fg-2" aria-live="polite">
              {job.status === 'queued'
                ? t('processing.queued')
                : [
                    `${pct}%`,
                    job.fps ? t('processing.fps', { fps: Math.round(job.fps) }) : null,
                    job.eta_s
                      ? t('processing.eta', { time: formatTimecode(job.eta_s * 1000) })
                      : null,
                  ]
                    .filter(Boolean)
                    .join(' · ')}
            </p>
            {segs && segs.total > 0 && (
              <SegmentBar done={segs.done} running={segs.running} total={segs.total} />
            )}
            <Banner tone="info">{t('processing.body')}</Banner>
          </>
        )}

        {failed && job.error && <FailureBanner job={job} videoId={videoId} canEdit={canEdit} />}
        {cancelled && <Banner tone="warn">{t('processing.cancelled')}</Banner>}
        {actionError !== null && <Banner tone="err">{actionMessage}</Banner>}

        <div className="flex flex-wrap justify-end gap-2">
          {canEdit && running && (
            <Button
              variant="danger"
              disabled={busy}
              onClick={() =>
                confirmCancel
                  ? void run(async () => toLatestJob(await cancelJob(job.id)))
                  : setConfirmCancel(true)
              }
            >
              {confirmCancel ? t('processing.confirmCancel') : t('processing.cancel')}
            </Button>
          )}
          {canEdit && failed && canRetry(job) && (
            <Button
              variant="primary"
              disabled={busy}
              onClick={() => void run(async () => toLatestJob(await retryJob(job.id)))}
            >
              {t('processing.retry')}
            </Button>
          )}
          <Button asChild>
            <Link to="/library">{t('processing.backToLibrary')}</Link>
          </Button>
        </div>
      </Card>
    </Page>
  );
}

function SegmentBar({ done, running, total }: { done: number; running: number; total: number }) {
  const { t } = useTranslation('video');
  const n = Math.min(total, MAX_SEGMENT_BLOCKS);
  const scale = n / total;
  const d = Math.round(done * scale);
  const r = Math.round(running * scale);
  return (
    <div className="flex items-center gap-3">
      <span className="text-caption text-fg-3">{t('processing.segments')}</span>
      <div
        className="flex flex-1 flex-wrap gap-[3px]"
        role="img"
        aria-label={t('processing.segmentsLabel', { done, total })}
      >
        {Array.from({ length: n }, (_, i) => (
          <span
            key={i}
            className={`h-2.5 w-2.5 rounded-[2px] ${i < d ? 'bg-accent' : i < d + r ? 'bg-info' : 'bg-line-strong'}`}
          />
        ))}
      </div>
      <span className="font-mono text-caption text-fg-2">
        {done}/{total}
      </span>
    </div>
  );
}

function FailureBanner({
  job,
  videoId,
  canEdit,
}: {
  job: LiveJob;
  videoId: string;
  canEdit: boolean;
}) {
  const { t } = useTranslation('video');
  const returnTo = `/videos/${encodeURIComponent(videoId)}/processing`;
  const { t: te } = useTranslation('errors');
  const err = job.error;
  if (!err) return null;
  const where =
    job.stage && job.stage in STEP_OF
      ? t(`processing.step.${STEP_OF[job.stage as JobStage]}`)
      : null;
  return (
    <Banner
      tone="err"
      actions={
        // E10 / E11: manual fallbacks for the two "we couldn't do it automatically" failures.
        canEdit && err.code === 'TEAM_SEPARATION_FAILED' ? (
          <Button asChild size="sm">
            <Link
              to="/videos/$videoId/teams"
              params={{ videoId }}
              search={{ mode: 'manual', return_to: returnTo }}
            >
              {t('processing.pickManually')}
            </Link>
          </Button>
        ) : canEdit && err.code === 'HOMOGRAPHY_UNAVAILABLE' ? (
          <Button asChild size="sm">
            <Link
              to="/videos/$videoId/calibrate"
              params={{ videoId }}
              search={{ return_to: returnTo }}
            >
              {t('processing.calibrateManually')}
            </Link>
          </Button>
        ) : null
      }
    >
      <strong>{where ? t('processing.failedAt', { step: where }) : t('processing.failed')}</strong>{' '}
      {te(`codes.${err.code}`, { defaultValue: err.code })}
      {err.detail ? ` ${err.detail}` : ''} {err.retryable ? t('processing.progressSaved') : ''}
    </Banner>
  );
}
