import { useInfiniteQuery, useQueries, useQueryClient } from '@tanstack/react-query';
import { getRouteApi, Link } from '@tanstack/react-router';
import { LayoutGrid, List, Search, Upload, Video } from 'lucide-react';
import { useEffect, useMemo, useRef, useState } from 'react';
import { useTranslation } from 'react-i18next';
import { Page, PageHeader } from '@/app/Page';
import { isApiError } from '@/api/errors';
import { retryJob, type Job } from '@/api/jobs';
import { latestJobQuery, videoKeys, videosInfiniteQuery, type Video as VideoT } from '@/api/videos';
import { RequireRole } from '@/auth/RequireRole';
import { Banner, Button, EmptyState, Input, SegmentedControl, Select, Skeleton, toast } from '@/ui';
import { YoutubeIcon } from '@/ui/icons';
import {
  applyLibraryFilters,
  cardStatus,
  hasJobs,
  hasActiveFilters,
  SORTS,
  SOURCE_FILTERS,
  STATUS_FILTERS,
  type LibrarySort,
  type SourceFilter,
  type StatusFilter,
} from './model';
import { NewAnalysisDialog } from './NewAnalysisDialog';
import { usePendingDeletes } from './pendingDeletes';
import { VideoCard, type VideoAction } from './VideoCard';
import { DeleteVideoDialog, RenameVideoDialog } from './VideoDialogs';

const route = getRouteApi('/_app/library');
const dialogState = { pvDialog: 'new' } as never;
const VIEW_KEY = 'pv-library-view';
type View = 'grid' | 'list';

function readView(): View {
  try {
    return localStorage.getItem(VIEW_KEY) === 'list' ? 'list' : 'grid';
  } catch {
    return 'grid';
  }
}

/** SCR-10 Library: every video of the active org, newest first, paged by cursor. */
export function LibraryPage() {
  const { t } = useTranslation('library');
  const { t: tc } = useTranslation();
  const search = route.useSearch();
  const navigate = route.useNavigate();
  const qc = useQueryClient();
  const [view, setView] = useState<View>(readView);
  const [dialog, setDialog] = useState<{ kind: 'rename' | 'delete'; video: VideoT } | null>(null);
  const pending = usePendingDeletes((s) => s.ids);

  const query = useInfiniteQuery(videosInfiniteQuery);
  const loaded = useMemo(
    () => (query.data?.pages.flatMap((p) => p.items) ?? []).filter((v) => !pending.has(v.id)),
    [query.data, pending],
  );
  // Filtering by status group only needs VideoOut.status (queued vs analyzing, degraded vs
  // ready stay in the same group), so job / manifest caches aren't consulted here.
  // The status filter needs each video's latest job (shared cache keys with the cards).
  // API request: a list-level `latest_job` on VideoOut would save these per-video calls.
  const jobs = useQueries({
    queries: loaded.map((v) => ({ ...latestJobQuery(v.id), enabled: hasJobs(v) })),
    combine: (results) => results.map((r) => r.data),
  });
  const visible = useMemo(() => {
    const byId = new Map(loaded.map((v, i) => [v.id, jobs[i]]));
    return applyLibraryFilters(loaded, search, (v) =>
      cardStatus(v, hasJobs(v) ? byId.get(v.id) : null),
    );
  }, [loaded, search, jobs]);

  const setSearch = (patch: Partial<typeof search>) =>
    void navigate({ search: (prev) => ({ ...prev, ...patch }), replace: true });

  const changeView = (v: View) => {
    setView(v);
    try {
      localStorage.setItem(VIEW_KEY, v);
    } catch {
      /* private mode */
    }
  };

  const onAction = (action: VideoAction, video: VideoT, job?: Job) => {
    if (action === 'retry') {
      if (job) void retry(video, job);
    } else setDialog({ kind: action, video });
  };

  const retry = async (video: VideoT, job: Job) => {
    try {
      const next = await retryJob(job.id);
      // The card re-subscribes to job:{id} once the job runs again.
      qc.setQueryData(videoKeys.latestJob(video.id), next);
      toast({ title: t('retry.started'), tone: 'success' });
    } catch (e) {
      toast({
        title: t('retry.failed'),
        description: isApiError(e) ? e.message : undefined,
        tone: 'error',
      });
    }
  };

  const total = loaded.length;
  const isEmpty = query.isSuccess && total === 0 && !query.hasNextPage;

  return (
    <Page>
      <PageHeader
        title={t('title')}
        meta={
          query.isSuccess
            ? query.hasNextPage
              ? t('countMore', { count: total })
              : t('count', { count: total })
            : undefined
        }
      />
      {!isEmpty && (
        <Toolbar search={search} setSearch={setSearch} view={view} onViewChange={changeView} />
      )}

      {query.isPending ? (
        <CardGrid view={view} aria-busy="true" aria-label={t('list.loading')}>
          {Array.from({ length: 8 }, (_, i) => (
            <Skeleton
              key={i}
              className={view === 'grid' ? 'aspect-[16/12] rounded-xl' : 'h-[92px] rounded-xl'}
            />
          ))}
        </CardGrid>
      ) : query.isError ? (
        <Banner
          tone="err"
          actions={
            <Button size="sm" onClick={() => void query.refetch()}>
              {tc('actions.retry')}
            </Button>
          }
        >
          {t('list.error')}
        </Banner>
      ) : isEmpty ? (
        <EmptyLibrary />
      ) : (
        <>
          {visible.length === 0 ? (
            <EmptyState
              variant="plain"
              icon={<Search size={26} />}
              title={t('list.noResults')}
              actions={
                hasActiveFilters(search) ? (
                  <Button
                    onClick={() =>
                      setSearch({ q: undefined, status: undefined, source: undefined })
                    }
                  >
                    {t('list.clearFilters')}
                  </Button>
                ) : undefined
              }
            />
          ) : (
            <CardGrid view={view} aria-label={t('list.label')}>
              {visible.map((v) => (
                <li key={v.id} className="list-none">
                  <VideoCard video={v} view={view} onAction={onAction} />
                </li>
              ))}
            </CardGrid>
          )}
          <LoadMore
            hasNext={query.hasNextPage}
            loading={query.isFetchingNextPage}
            onLoad={() => void query.fetchNextPage()}
          />
        </>
      )}

      <RenameVideoDialog
        video={dialog?.kind === 'rename' ? dialog.video : null}
        onOpenChange={(o) => !o && setDialog(null)}
      />
      <DeleteVideoDialog
        video={dialog?.kind === 'delete' ? dialog.video : null}
        onOpenChange={(o) => !o && setDialog(null)}
      />
      <RequireRole min="editor">
        <NewAnalysisDialog />
      </RequireRole>
    </Page>
  );
}

function CardGrid({
  view,
  children,
  ...rest
}: {
  view: View;
  children: React.ReactNode;
  'aria-label'?: string;
  'aria-busy'?: 'true';
}) {
  return (
    <ul
      className={
        view === 'grid'
          ? 'm-0 grid grid-cols-[repeat(auto-fill,minmax(250px,1fr))] gap-4 p-0'
          : 'm-0 flex flex-col gap-2.5 p-0'
      }
      {...rest}
    >
      {children}
    </ul>
  );
}

function Toolbar({
  search,
  setSearch,
  view,
  onViewChange,
}: {
  search: { q?: string; status?: StatusFilter; source?: SourceFilter; sort?: LibrarySort };
  setSearch: (patch: {
    q?: string;
    status?: StatusFilter;
    source?: SourceFilter;
    sort?: LibrarySort;
  }) => void;
  view: View;
  onViewChange: (v: View) => void;
}) {
  const { t } = useTranslation('library');
  const [q, setQ] = useState(search.q ?? '');
  const latest = useRef(setSearch);
  useEffect(() => {
    latest.current = setSearch;
  });
  // E01: debounce 300 ms, then mirror into ?q=.
  useEffect(() => {
    const next = q.trim() || undefined;
    if (next === search.q) return;
    const id = setTimeout(() => latest.current({ q: next }), 300);
    return () => clearTimeout(id);
  }, [q, search.q]);
  // External changes (Back button, "Clear filters") flow back into the field.
  const [seenQ, setSeenQ] = useState(search.q);
  if (seenQ !== search.q) {
    setSeenQ(search.q);
    if ((search.q ?? '') !== q.trim()) setQ(search.q ?? '');
  }

  return (
    <div className="mb-5 flex flex-wrap items-center gap-2.5">
      <div className="min-w-[220px] flex-1 sm:max-w-[340px]">
        <Input
          type="search"
          value={q}
          onChange={(e) => setQ(e.target.value)}
          placeholder={t('toolbar.search')}
          aria-label={t('toolbar.searchLabel')}
          leading={<Search size={16} />}
        />
      </div>
      <Select
        aria-label={t('toolbar.status')}
        className="w-auto min-w-[150px]"
        value={search.status ?? 'all'}
        onValueChange={(v) => setSearch({ status: v === 'all' ? undefined : (v as StatusFilter) })}
        options={STATUS_FILTERS.map((s) => ({ value: s, label: t(`toolbar.statusOptions.${s}`) }))}
      />
      <Select
        aria-label={t('toolbar.source')}
        className="w-auto min-w-[140px]"
        value={search.source ?? 'all'}
        onValueChange={(v) => setSearch({ source: v === 'all' ? undefined : (v as SourceFilter) })}
        options={SOURCE_FILTERS.map((s) => ({ value: s, label: t(`toolbar.sourceOptions.${s}`) }))}
      />
      <Select
        aria-label={t('toolbar.sort')}
        className="w-auto min-w-[130px]"
        value={search.sort ?? 'newest'}
        onValueChange={(v) => setSearch({ sort: v === 'newest' ? undefined : (v as LibrarySort) })}
        options={SORTS.map((s) => ({ value: s, label: t(`toolbar.sortOptions.${s}`) }))}
      />
      <div className="flex-1" />
      <SegmentedControl<View>
        aria-label={t('toolbar.view')}
        value={view}
        onValueChange={onViewChange}
        options={[
          {
            value: 'grid',
            label: (
              <>
                <LayoutGrid size={15} aria-hidden="true" />
                <span className="sr-only">{t('toolbar.grid')}</span>
              </>
            ),
          },
          {
            value: 'list',
            label: (
              <>
                <List size={15} aria-hidden="true" />
                <span className="sr-only">{t('toolbar.list')}</span>
              </>
            ),
          },
        ]}
      />
    </div>
  );
}

/** Infinite scroll: a sentinel loads the next page; the button is the keyboard / no-IO fallback. */
function LoadMore({
  hasNext,
  loading,
  onLoad,
}: {
  hasNext: boolean;
  loading: boolean;
  onLoad: () => void;
}) {
  const { t } = useTranslation('library');
  const ref = useRef<HTMLDivElement>(null);
  const latest = useRef(onLoad);
  useEffect(() => {
    latest.current = onLoad;
  });
  useEffect(() => {
    const el = ref.current;
    if (!el || !hasNext || typeof IntersectionObserver === 'undefined') return;
    const io = new IntersectionObserver(
      (entries) => {
        if (entries.some((e) => e.isIntersecting)) latest.current();
      },
      { rootMargin: '400px 0px' },
    );
    io.observe(el);
    return () => io.disconnect();
  }, [hasNext]);

  if (!hasNext) return null;
  return (
    <div ref={ref} className="mt-6 flex justify-center">
      <Button onClick={onLoad} loading={loading}>
        {t('list.loadMore')}
      </Button>
    </div>
  );
}

function EmptyLibrary() {
  const { t } = useTranslation('library');
  return (
    <EmptyState
      icon={<Video size={30} />}
      title={t('empty.title')}
      description={t('empty.body')}
      actions={
        <RequireRole min="editor">
          <Button asChild variant="primary" size="lg">
            <Link to="/library" search={{ new: 'youtube' }} state={dialogState}>
              <YoutubeIcon size={18} />
              {t('empty.youtube')}
            </Link>
          </Button>
          <Button asChild size="lg">
            <Link to="/library" search={{ new: 'upload' }} state={dialogState}>
              <Upload size={18} aria-hidden="true" />
              {t('empty.upload')}
            </Link>
          </Button>
        </RequireRole>
      }
    />
  );
}
