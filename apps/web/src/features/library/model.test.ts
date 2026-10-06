import { canRetry } from '@/api/jobs';
import { applyJobEvent } from '@/api/useJobProgress';
import { mockJob, mockLatestJob, mockVideo } from '@/mocks/videos';
import { applyLibraryFilters, cardStatus, cardTarget } from './model';

describe('library model', () => {
  it('maps video status + latest job to the card badge (UIUX E10)', () => {
    expect(cardStatus({ status: 'uploading' })).toBe('uploading');
    expect(cardStatus({ status: 'ingesting' })).toBe('ingesting');
    expect(cardStatus({ status: 'failed' })).toBe('failed'); // ingest failure
    // Source ready: the job decides.
    expect(cardStatus({ status: 'ready' }, null)).toBe('not_analyzed');
    expect(cardStatus({ status: 'ready' }, { status: 'queued' })).toBe('queued');
    expect(cardStatus({ status: 'ready' }, { status: 'calibrating' })).toBe('analyzing');
    expect(cardStatus({ status: 'ready' }, { status: 'done' })).toBe('ready');
    expect(cardStatus({ status: 'ready' }, { status: 'failed' })).toBe('failed');
    expect(cardStatus({ status: 'ready' }, { status: 'cancelled' })).toBe('cancelled');
    // Video statuses the API may set later still work without a job.
    expect(cardStatus({ status: 'done' })).toBe('ready');
    expect(cardStatus({ status: 'processing' })).toBe('analyzing');
  });

  it('routes cards by status', () => {
    expect(cardTarget('v', 'uploading')).toBeNull();
    expect(cardTarget('v', 'ready')).toEqual({ kind: 'workspace', videoId: 'v' });
    expect(cardTarget('v', 'failed')).toEqual({ kind: 'processing', videoId: 'v' });
    expect(cardTarget('v', 'analyzing')).toEqual({ kind: 'processing', videoId: 'v' });
  });

  it('filters, searches and sorts', () => {
    const a = mockVideo({ title: 'Zeta', created_at: '2026-10-01T00:00:00Z', duration_ms: 10 });
    const b = mockVideo({
      title: 'alpha',
      created_at: '2026-10-03T00:00:00Z',
      duration_ms: 30,
      source_type: 'upload',
    });
    const c = mockVideo({ title: 'Mid', created_at: '2026-10-02T00:00:00Z', status: 'failed' });
    const d = mockVideo({ title: 'Gone', status: 'deleted' });
    const all = [a, b, c, d];
    const titles = (f: Parameters<typeof applyLibraryFilters>[1]) =>
      applyLibraryFilters(all, f).map((v) => v.title);
    expect(titles({})).toEqual(['alpha', 'Mid', 'Zeta']);
    expect(titles({ sort: 'oldest' })).toEqual(['Zeta', 'Mid', 'alpha']);
    expect(titles({ sort: 'title' })).toEqual(['alpha', 'Mid', 'Zeta']);
    expect(titles({ sort: 'duration' })[0]).toBe('Mid'); // 5 652 000 ms default
    expect(titles({ status: 'failed' })).toEqual(['Mid']);
    expect(titles({ status: 'ready', source: 'upload' })).toEqual(['alpha']);
    expect(titles({ q: '  ZET ' })).toEqual(['Zeta']);
  });

  it('shows "Low pitch quality" for a finished job with degraded calibration', () => {
    const ready = { status: 'ready' } as const;
    expect(cardStatus(ready, { status: 'done', pitch_calibration: 'degraded' })).toBe(
      'ready_degraded',
    );
    expect(cardStatus(ready, { status: 'done', pitch_calibration: 'ok' })).toBe('ready');
    expect(cardStatus(ready, { status: 'done', pitch_calibration: null })).toBe('ready');
    expect(cardTarget('v', 'ready_degraded')).toEqual({ kind: 'workspace', videoId: 'v' });
    expect(cardStatus(ready, { status: 'done', pitch_calibration: 'unavailable' })).toBe(
      'ready_no_pitch',
    );
    expect(cardTarget('v', 'ready_no_pitch')).toEqual({ kind: 'workspace', videoId: 'v' });
    // The status filter reads `latest_job` straight from the list item.
    const v = mockVideo({
      status: 'ready',
      latest_job: mockLatestJob(
        mockJob(mockVideo(), { status: 'done', pitch_calibration: 'degraded' }),
      ),
    });
    expect(applyLibraryFilters([v], { status: 'ready' })).toEqual([v]);
    expect(applyLibraryFilters([v], { status: 'failed' })).toEqual([]);
  });

  it('applies WS events to a job snapshot', () => {
    const job = mockLatestJob(mockJob(mockVideo(), { status: 'queued', progress: 0, stage: null }));
    const p = applyJobEvent(job, {
      topic: 't',
      type: 'progress',
      data: { stage: 'infer', pct: 12.5, eta_s: 60, segments: { done: 3, running: 4, total: 45 } },
    });
    expect(p).toMatchObject({
      status: 'inferring',
      stage: 'infer',
      progress: 12.5,
      eta_s: 60,
      segments: { done: 3, running: 4, total: 45 },
    });
    expect(
      applyJobEvent(job, { topic: 't', type: 'progress', data: { stage: 'tiles', pct: 96 } }),
    ).toMatchObject({ status: 'postprocessing' });
    expect(
      applyJobEvent(p, { topic: 't', type: 'completed', data: { video_id: 'v' } }),
    ).toMatchObject({ status: 'done', progress: 100 });
    expect(
      applyJobEvent(p, { topic: 't', type: 'failed', data: { code: 'INTERNAL', retryable: true } }),
    ).toMatchObject({ status: 'failed', error: { code: 'INTERNAL', retryable: true } });
    expect(
      applyJobEvent(p, { topic: 't', type: 'cancelled', data: { video_id: 'v' } }),
    ).toMatchObject({ status: 'cancelled', eta_s: null, progress: 12.5 });
    expect(canRetry({ status: 'failed', error: { code: 'INTERNAL', retryable: false } })).toBe(
      false,
    );
    expect(canRetry({ status: 'failed', error: { code: 'INTERNAL', retryable: true } })).toBe(true);
    expect(canRetry({ status: 'done', error: null })).toBe(false);
  });
});
