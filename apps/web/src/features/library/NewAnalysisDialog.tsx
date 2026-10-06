import { useNavigate } from '@tanstack/react-router';
import { FileVideo, Upload, X } from 'lucide-react';
import {
  useEffect,
  useRef,
  useState,
  type ChangeEvent,
  type DragEvent,
  type ReactNode,
} from 'react';
import { useTranslation } from 'react-i18next';
import { isApiError } from '@/api/errors';
import {
  MAX_DURATION_S,
  MAX_UPLOAD_BYTES,
  VIDEO_EXTENSIONS,
  createFromUrl,
  createJob,
  previewYoutube,
  type UrlPreview,
} from '@/api/uploads';
import { useApiErrorMessage } from '@/api/useApiErrorMessage';
import { uploads, useUpload } from '@/features/upload/useUploads';
import { formatBytes, formatTimecode } from '@/i18n/format';
import { useSearchDialog } from '@/lib/useSearchDialog';
import type { NewAnalysisSource } from '@/router';
import {
  Banner,
  Button,
  Checkbox,
  Dialog,
  Field,
  Input,
  Progress,
  Spinner,
  Tabs,
  TabsContent,
  TabsList,
  TabsTrigger,
  toast,
} from '@/ui';
import { YoutubeIcon } from '@/ui/icons';
import { AdvancedSettingsDialog } from './AdvancedSettingsDialog';
import { toJobConfig, useAnalysisDefaults, type AnalysisSettings } from './analysisSettings';

const YOUTUBE_URL = /^(https?:\/\/)?((www|m)\.)?(youtube\.com|youtu\.be)\//i;
const titleFromFilename = (name: string) =>
  name
    .replace(/\.[^.]+$/, '')
    .replace(/[_]+/g, ' ')
    .trim();

/** Best-effort duration from the file's own metadata; null when the browser can't read the container. */
function probeDuration(file: File): Promise<number | null> {
  return new Promise((resolve) => {
    if (typeof document === 'undefined' || typeof URL.createObjectURL !== 'function')
      return resolve(null);
    const v = document.createElement('video');
    const url = URL.createObjectURL(file);
    const done = (d: number | null) => {
      URL.revokeObjectURL(url);
      resolve(d);
    };
    v.preload = 'metadata';
    v.onloadedmetadata = () => done(Number.isFinite(v.duration) ? v.duration : null);
    v.onerror = () => done(null);
    setTimeout(() => done(null), 5000);
    v.src = url;
  });
}

/**
 * SCR-11 New analysis (modal lg) bound to `?new=youtube|upload` — M1-FE-09.
 * YouTube is the default tab (PLAN v1.2, broadcast-first); Upload sends parts straight to storage and
 * keeps going in the background (E15); Import is "Soon"; Live is Phase C and not rendered.
 */
export function NewAnalysisDialog() {
  const { t } = useTranslation('library');
  const dialog = useSearchDialog<NewAnalysisSource>('new');
  const navigate = useNavigate();
  const tab = dialog.value ?? 'youtube';
  const defaults = useAnalysisDefaults((st) => st.settings);
  const [settings, setSettings] = useState<AnalysisSettings>(defaults);
  const [advancedOpen, setAdvancedOpen] = useState(false);
  const summary = <ModeSummary settings={settings} onEdit={() => setAdvancedOpen(true)} />;

  const openProcessing = (videoId: string) => {
    dialog.close();
    void navigate({ to: '/videos/$videoId/processing', params: { videoId } });
  };

  return (
    <Dialog
      open={dialog.isOpen}
      onOpenChange={dialog.onOpenChange}
      title={t('newAnalysis.title')}
      description={t('newAnalysis.description')}
      size="lg"
    >
      <Tabs value={tab} onValueChange={(v) => void dialog.open(v as NewAnalysisSource)}>
        <TabsList aria-label={t('newAnalysis.tabs.label')}>
          <TabsTrigger value="youtube" icon={<YoutubeIcon size={16} />}>
            {t('newAnalysis.tabs.youtube')}
          </TabsTrigger>
          <TabsTrigger value="upload" icon={<Upload size={16} />}>
            {t('newAnalysis.tabs.upload')}
          </TabsTrigger>
          <TabsTrigger value="import" disabled soon={t('newAnalysis.soon')}>
            {t('newAnalysis.tabs.import')}
          </TabsTrigger>
        </TabsList>
        <TabsContent value="youtube" className="flex flex-col gap-4">
          <YoutubeTab
            onCancel={dialog.close}
            onStarted={openProcessing}
            settings={settings}
            summary={summary}
          />
        </TabsContent>
        <TabsContent value="upload" className="flex flex-col gap-4">
          <UploadTab
            onCancel={dialog.close}
            onBackground={dialog.close}
            onAnalyzing={openProcessing}
            settings={settings}
            summary={summary}
          />
        </TabsContent>
      </Tabs>
      <AdvancedSettingsDialog
        open={advancedOpen}
        onOpenChange={setAdvancedOpen}
        value={settings}
        onApply={setSettings}
      />
    </Dialog>
  );
}

/** "Standard mode · confidence 0.35 · Advanced settings" (SCR-11 E11/E20 → SCR-12). */
function ModeSummary({ settings, onEdit }: { settings: AnalysisSettings; onEdit: () => void }) {
  const { t } = useTranslation('library');
  return (
    <p className="m-0 text-caption text-fg-3">
      {t('newAnalysis.modeSummary', {
        mode: t(`settings.${settings.mode}`),
        conf: settings.confidence.toFixed(2),
      })}{' '}
      ·{' '}
      <button type="button" className="bg-transparent p-0 text-accent underline" onClick={onEdit}>
        {t('newAnalysis.advanced')}
      </button>
    </p>
  );
}

/* ------------------------------------------------------------------ YouTube (E16–E22) */

function YoutubeTab({
  onCancel,
  onStarted,
  settings,
  summary,
}: {
  onCancel: () => void;
  onStarted: (videoId: string) => void;
  settings: AnalysisSettings;
  summary: ReactNode;
}) {
  const { t } = useTranslation('library');
  const { t: tc } = useTranslation();
  const [url, setUrl] = useState('');
  const [preview, setPreview] = useState<UrlPreview | null>(null);
  const [title, setTitle] = useState('');
  const [rights, setRights] = useState(false);
  const [fetching, setFetching] = useState(false);
  const [submitting, setSubmitting] = useState(false);
  const [error, setError] = useState<unknown>(null);
  const [invalid, setInvalid] = useState(false);
  const errorMessage = useApiErrorMessage(error);
  const abort = useRef<AbortController | null>(null);

  useEffect(() => () => abort.current?.abort(), []);

  const fetchPreview = async (value = url) => {
    const candidate = value.trim();
    setPreview(null);
    setError(null);
    if (!YOUTUBE_URL.test(candidate)) {
      setInvalid(true);
      return;
    }
    setInvalid(false);
    abort.current?.abort();
    abort.current = new AbortController();
    setFetching(true);
    try {
      const p = await previewYoutube(candidate, abort.current.signal);
      setPreview(p);
      setTitle(p.title ?? '');
    } catch (e) {
      if (!abort.current.signal.aborted) setError(e);
    } finally {
      setFetching(false);
    }
  };

  const analyze = async () => {
    if (!preview || !rights) return;
    setSubmitting(true);
    setError(null);
    try {
      const video = await createFromUrl(preview.url, title.trim() || null);
      await createJob(video.id, toJobConfig(settings));
      onStarted(video.id);
    } catch (e) {
      setError(e);
      setSubmitting(false);
    }
  };

  return (
    <>
      <div className="flex items-start gap-2">
        <Field
          label={t('newAnalysis.youtube.label')}
          help={t('newAnalysis.youtube.help')}
          error={invalid ? t('newAnalysis.youtube.invalid') : undefined}
          className="flex-1"
        >
          <Input
            type="url"
            inputMode="url"
            autoFocus
            value={url}
            placeholder={t('newAnalysis.youtube.placeholder')}
            leading={<YoutubeIcon size={16} />}
            onChange={(e: ChangeEvent<HTMLInputElement>) => setUrl(e.target.value)}
            onPaste={(e) => {
              const pasted = e.clipboardData.getData('text');
              if (pasted) setTimeout(() => void fetchPreview(pasted), 0); // E16: paste triggers E17
            }}
            onKeyDown={(e) => {
              if (e.key === 'Enter') {
                e.preventDefault();
                void fetchPreview();
              }
            }}
          />
        </Field>
        <Button
          className="mt-[26px]"
          onClick={() => void fetchPreview()}
          disabled={!url.trim() || fetching}
        >
          {fetching ? <Spinner size={14} /> : t('newAnalysis.youtube.fetch')}
        </Button>
      </div>

      {error !== null && (
        <Banner tone="err">
          {errorMessage}
          {isApiError(error) && error.detail ? ` ${error.detail}` : ''}
        </Banner>
      )}

      {preview && (
        <div
          className="flex gap-3 rounded-xl border border-line bg-surface-2 p-3"
          data-testid="yt-preview"
        >
          {preview.thumbnail_url && (
            <img
              src={preview.thumbnail_url}
              alt=""
              className="h-[68px] w-[120px] rounded-lg object-cover"
            />
          )}
          <div className="text-body-sm min-w-0">
            <p className="m-0 truncate font-semibold">{preview.title}</p>
            <p className="m-0 text-fg-3">
              {[
                preview.duration_s ? formatTimecode(preview.duration_s * 1000) : null,
                preview.height ? `${preview.height}p` : null,
                preview.fps ? `${Math.round(preview.fps)} fps` : null,
              ]
                .filter(Boolean)
                .join(' · ')}
            </p>
          </div>
        </div>
      )}

      {preview && (
        <Field label={t('newAnalysis.titleLabel')}>
          <Input
            value={title}
            maxLength={200}
            onChange={(e: ChangeEvent<HTMLInputElement>) => setTitle(e.target.value)}
          />
        </Field>
      )}
      <Checkbox
        label={t('newAnalysis.rights')}
        checked={rights}
        onCheckedChange={(c) => setRights(c === true)}
      />
      {summary}
      <div className="flex justify-end gap-2">
        <Button onClick={onCancel}>{tc('actions.cancel')}</Button>
        <Button
          variant="primary"
          disabled={!preview || !rights || submitting}
          onClick={() => void analyze()}
        >
          {submitting ? <Spinner size={14} /> : t('newAnalysis.submit')}
        </Button>
      </div>
    </>
  );
}

/* ------------------------------------------------------------------ Upload (E06–E15) */

type FileError = 'format' | 'size' | 'duration';

function UploadTab({
  onCancel,
  onBackground,
  onAnalyzing,
  settings,
  summary,
}: {
  onCancel: () => void;
  onBackground: () => void;
  onAnalyzing: (videoId: string) => void;
  settings: AnalysisSettings;
  summary: ReactNode;
}) {
  const { t, i18n } = useTranslation('library');
  const { t: tc } = useTranslation();
  const [file, setFile] = useState<File | null>(null);
  const [fileError, setFileError] = useState<FileError | null>(null);
  const [duration, setDuration] = useState<number | null>(null);
  const [title, setTitle] = useState('');
  const [rights, setRights] = useState(false);
  const [autoAnalyze, setAutoAnalyze] = useState(true);
  const [uploadId, setUploadId] = useState<string | null>(null);
  const [confirmCancel, setConfirmCancel] = useState(false);
  const [dragging, setDragging] = useState(false);
  const input = useRef<HTMLInputElement>(null);
  const item = useUpload(uploadId);
  const errorMessage = useApiErrorMessage(item?.phase === 'failed' ? item.error : null);

  const choose = async (f: File | undefined) => {
    if (!f) return;
    const ext = f.name.split('.').pop()?.toLowerCase() ?? '';
    setDuration(null);
    if (!(VIDEO_EXTENSIONS as readonly string[]).includes(ext)) return reject(f, 'format');
    if (f.size > MAX_UPLOAD_BYTES) return reject(f, 'size');
    setFile(f);
    setFileError(null);
    setTitle(titleFromFilename(f.name));
    const d = await probeDuration(f);
    setDuration(d);
    if (d !== null && d > MAX_DURATION_S) setFileError('duration');
  };
  const reject = (f: File, why: FileError) => {
    setFile(f);
    setFileError(why);
  };

  const start = () => {
    if (!file || fileError || !rights) return;
    const { id, done } = uploads.start(file, {
      title: title.trim() || null,
      autoAnalyze,
      jobConfig: toJobConfig(settings),
    });
    setUploadId(id);
    done.then(
      ({ videoId }) => {
        if (autoAnalyze) onAnalyzing(videoId);
        else {
          onBackground();
          toast({
            tone: 'success',
            title: t('newAnalysis.upload.savedToast', { title: title || file.name }),
          });
        }
      },
      () => undefined, // failure is rendered from the manager state
    );
  };

  // Upload in progress (or just failed) → progress view (prototype "trạng thái đang upload").
  if (item && item.phase !== 'cancelled') {
    const pct = item.size ? Math.round((100 * item.sent) / item.size) : 0;
    const failed = item.phase === 'failed';
    return (
      <>
        <div className="text-body-sm flex items-center gap-2">
          <FileVideo size={18} aria-hidden="true" className="text-fg-3" />
          <span className="truncate font-semibold">{item.filename}</span>
          <span className="text-fg-3">· {formatBytes(i18n.language, item.size)}</span>
        </div>
        <Progress
          value={failed ? pct : item.phase === 'completing' ? null : pct}
          label={t('newAnalysis.upload.progressLabel')}
          tone={failed ? 'danger' : 'accent'}
        />
        <p className="text-body-sm m-0 text-fg-2" aria-live="polite">
          {failed
            ? errorMessage
            : item.phase === 'completing'
              ? t('newAnalysis.upload.finishing')
              : t('newAnalysis.upload.progress', {
                  pct,
                  speed: formatBytes(i18n.language, item.bytesPerSec),
                  eta: item.etaS === null ? '–' : formatTimecode(item.etaS * 1000),
                })}
          {item.resumedParts > 0 && !failed
            ? ` ${t('newAnalysis.upload.resumed', { count: item.resumedParts })}`
            : ''}
        </p>
        <div className="flex justify-end gap-2">
          {failed ? (
            <>
              <Button onClick={() => setUploadId(null)}>{tc('actions.back')}</Button>
              <Button
                variant="primary"
                onClick={() => {
                  setUploadId(null);
                  start();
                }}
              >
                {t('newAnalysis.upload.retry')}
              </Button>
            </>
          ) : (
            <>
              <Button
                variant={confirmCancel ? 'danger' : 'secondary'}
                onClick={() => {
                  if (!confirmCancel) return setConfirmCancel(true);
                  void uploads.cancel(item.id);
                  setUploadId(null);
                  setConfirmCancel(false);
                  onCancel();
                }}
              >
                {confirmCancel
                  ? t('newAnalysis.upload.confirmCancel')
                  : t('newAnalysis.upload.cancel')}
              </Button>
              <Button variant="primary" onClick={onBackground}>
                {t('newAnalysis.upload.background')}
              </Button>
            </>
          )}
        </div>
      </>
    );
  }

  const onDrop = (e: DragEvent<HTMLDivElement>) => {
    e.preventDefault();
    setDragging(false);
    void choose(e.dataTransfer.files[0]);
  };

  return (
    <>
      {!file || fileError ? (
        <div
          role="button"
          tabIndex={0}
          data-testid="dropzone"
          onClick={() => input.current?.click()}
          onKeyDown={(e) => (e.key === 'Enter' || e.key === ' ') && input.current?.click()}
          onDragOver={(e) => {
            e.preventDefault();
            setDragging(true);
          }}
          onDragLeave={() => setDragging(false)}
          onDrop={onDrop}
          className={`cursor-pointer rounded-2xl border-[1.5px] border-dashed p-7 text-center text-fg-2 outline-none focus-visible:ring-2 focus-visible:ring-accent ${dragging ? 'border-accent bg-accent/10' : 'border-line-strong bg-surface-2'}`}
        >
          <Upload size={28} className="mx-auto mb-2.5 text-accent" aria-hidden="true" />
          <p className="m-0 font-semibold text-fg">{t('newAnalysis.upload.drop')}</p>
          <p className="m-0 text-caption text-fg-3">
            {t('newAnalysis.upload.formats', {
              size: formatBytes(i18n.language, MAX_UPLOAD_BYTES),
              hours: MAX_DURATION_S / 3600,
            })}
          </p>
          {fileError && file && (
            <p className="m-0 mt-2 text-caption text-danger" role="alert">
              {t(`newAnalysis.upload.errors.${fileError}`, { name: file.name })}
            </p>
          )}
        </div>
      ) : (
        <div className="text-body-sm flex items-center gap-2 rounded-xl border border-line bg-surface-2 p-3">
          <FileVideo size={18} aria-hidden="true" className="text-fg-3" />
          <span className="truncate font-semibold">{file.name}</span>
          <span className="text-fg-3">
            · {formatBytes(i18n.language, file.size)}
            {duration !== null ? ` · ${formatTimecode(duration * 1000)}` : ''}
          </span>
          <Button
            size="sm"
            variant="ghost"
            className="ml-auto"
            onClick={() => {
              setFile(null);
              setFileError(null);
            }}
          >
            <X size={14} aria-hidden="true" /> {t('newAnalysis.upload.remove')}
          </Button>
        </div>
      )}
      <input
        ref={input}
        type="file"
        hidden
        data-testid="file-input"
        accept={VIDEO_EXTENSIONS.map((e) => `.${e}`).join(',')}
        onChange={(e) => {
          void choose(e.target.files?.[0]);
          e.target.value = '';
        }}
      />
      {file && !fileError && (
        <Field label={t('newAnalysis.titleLabel')}>
          <Input
            value={title}
            maxLength={200}
            onChange={(e: ChangeEvent<HTMLInputElement>) => setTitle(e.target.value)}
          />
        </Field>
      )}
      <Checkbox
        label={t('newAnalysis.rightsUpload')}
        checked={rights}
        onCheckedChange={(c) => setRights(c === true)}
      />
      <Checkbox
        label={t('newAnalysis.upload.autoAnalyze')}
        checked={autoAnalyze}
        onCheckedChange={(c) => setAutoAnalyze(c === true)}
      />
      {summary}
      <div className="flex justify-end gap-2">
        <Button onClick={onCancel}>{tc('actions.cancel')}</Button>
        <Button variant="primary" disabled={!file || fileError !== null || !rights} onClick={start}>
          {t('newAnalysis.upload.start')}
        </Button>
      </div>
    </>
  );
}
