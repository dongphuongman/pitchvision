import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { Link, useNavigate } from '@tanstack/react-router';
import { useMemo, useState } from 'react';
import { useTranslation } from 'react-i18next';
import { createExport, exportKeys, type ExportLayers } from '@/api/exports';
import type { Manifest } from '@/api/types';
import { usageQuery } from '@/api/usage';
import { useApiErrorMessage } from '@/api/useApiErrorMessage';
import { formatTimecode } from '@/i18n/format';
import type { OverlayLayers } from '@/player/overlay';
import { parseTimecode } from './model';
import {
  Badge,
  Banner,
  Button,
  Checkbox,
  Dialog,
  DialogClose,
  Field,
  Input,
  SegmentedControl,
  toast,
} from '@/ui';

type RangeMode = 'full' | 'custom';
type Layout = 'video' | 'video_map';
type Quality = '720' | '1080';

const LAYER_KEYS = ['boxes', 'labels', 'conf', 'ball', 'trails'] as const;

export interface ExportDialogProps {
  open: boolean;
  onOpenChange: (open: boolean) => void;
  videoId: string;
  manifest: Manifest;
  /** Workspace overlay toggles (SCR-25 E07 defaults). */
  layers: OverlayLayers;
  /** Current playback position — the custom range starts here. */
  currentMs: number;
}

/**
 * SCR-25 Export (M1: Video tab only — Data M3, Report PDF M4, tactical-map video M2). Range (full
 * match / custom), layers, layout (video + map needs pitch calibration), quality (1080p = Pro,
 * Free shows the watermark notice + upgrade link). Start → toast "Export started — View exports".
 */
export function ExportDialog({
  open,
  onOpenChange,
  videoId,
  manifest,
  layers: overlay,
  currentMs,
}: ExportDialogProps) {
  const { t } = useTranslation('video');
  const navigate = useNavigate();
  const qc = useQueryClient();
  const usage = useQuery({ ...usageQuery, enabled: open });
  const proQuality = (usage.data?.export_max_height ?? 720) >= 1080;
  const watermark = usage.data?.export_watermark ?? true;
  const mapAvailable = manifest.pitch.status !== 'unavailable';

  const [mode, setMode] = useState<RangeMode>('full');
  const [from, setFrom] = useState(() => formatTimecode(currentMs));
  const [to, setTo] = useState(() =>
    formatTimecode(Math.min(manifest.duration_ms, currentMs + 60_000)),
  );
  const [layers, setLayers] = useState<ExportLayers>(() => ({ ...overlay, trails: false }));
  const [layout, setLayout] = useState<Layout>('video');
  const [quality, setQuality] = useState<Quality>('720');
  const [key] = useState(() => crypto.randomUUID());

  const range = useMemo(() => {
    if (mode === 'full') return { from_ms: null, to_ms: null, ok: true };
    const a = parseTimecode(from);
    const b = parseTimecode(to);
    const ok = a !== null && b !== null && a < b && a < manifest.duration_ms;
    return { from_ms: a, to_ms: b, ok };
  }, [mode, from, to, manifest.duration_ms]);

  const start = useMutation({
    mutationFn: () =>
      createExport(
        {
          video_id: videoId,
          kind: 'video',
          from_ms: range.from_ms,
          to_ms: range.to_ms,
          layers,
          layout,
          quality: quality === '1080' ? 1080 : 720,
        },
        key,
      ),
    onSuccess: () => {
      void qc.invalidateQueries({ queryKey: exportKeys.all });
      onOpenChange(false);
      toast({
        title: t('exports.dialog.started'),
        tone: 'success',
        action: {
          label: t('exports.dialog.viewExports'),
          onClick: () => void navigate({ to: '/exports' }),
        },
      });
    },
  });

  const errorMessage = useApiErrorMessage(start.error);

  const opt = (label: string, pro?: boolean) =>
    pro ? (
      <span className="inline-flex items-center gap-1.5">
        {label}
        <Badge tone="pro" size="sm">
          {t('exports.dialog.pro')}
        </Badge>
      </span>
    ) : (
      label
    );

  return (
    <Dialog
      open={open}
      onOpenChange={onOpenChange}
      size="md"
      title={t('exports.dialog.title')}
      footer={
        <>
          <DialogClose asChild>
            <Button variant="ghost">{t('exports.cancel')}</Button>
          </DialogClose>
          <Button
            variant="primary"
            disabled={!range.ok}
            loading={start.isPending}
            onClick={() => start.mutate()}
          >
            {t('exports.dialog.start')}
          </Button>
        </>
      }
    >
      <div className="flex flex-col gap-4">
        <SegmentedControl<'video' | 'data' | 'pdf' | 'map'>
          value="video"
          options={[
            { value: 'video', label: t('exports.dialog.tabs.video') },
            { value: 'data', label: t('exports.dialog.tabs.data'), disabled: true },
            { value: 'pdf', label: t('exports.dialog.tabs.pdf'), disabled: true },
            { value: 'map', label: t('exports.dialog.tabs.map'), disabled: true },
          ]}
        />

        <fieldset className="m-0 flex flex-col gap-2 border-0 p-0">
          <legend className="mb-1.5 text-control font-semibold">{t('exports.dialog.range')}</legend>
          <SegmentedControl<RangeMode>
            value={mode}
            onValueChange={setMode}
            options={[
              {
                value: 'full',
                label: `${t('exports.dialog.full')} · ${formatTimecode(manifest.duration_ms)}`,
              },
              { value: 'custom', label: t('exports.dialog.custom') },
            ]}
          />
          {mode === 'custom' && (
            <div className="flex items-start gap-3">
              <Field
                label={t('exports.dialog.from')}
                className="w-32"
                error={range.ok ? undefined : t('exports.dialog.rangeInvalid')}
              >
                <Input value={from} onChange={(e) => setFrom(e.target.value)} inputMode="numeric" />
              </Field>
              <Field
                label={t('exports.dialog.to')}
                className="w-32"
                help={t('exports.dialog.rangeHint')}
              >
                <Input value={to} onChange={(e) => setTo(e.target.value)} inputMode="numeric" />
              </Field>
            </div>
          )}
        </fieldset>

        <fieldset className="m-0 flex flex-col gap-2 border-0 p-0">
          <legend className="mb-1.5 text-control font-semibold">
            {t('exports.dialog.layers')}
          </legend>
          <div className="flex flex-wrap gap-x-5 gap-y-2">
            {LAYER_KEYS.map((k) => (
              <Checkbox
                key={k}
                label={t(`exports.dialog.layer.${k}`)}
                checked={layers[k]}
                onCheckedChange={(v) => setLayers((prev) => ({ ...prev, [k]: v === true }))}
              />
            ))}
          </div>
        </fieldset>

        <fieldset className="m-0 flex flex-col gap-2 border-0 p-0">
          <legend className="mb-1.5 text-control font-semibold">
            {t('exports.dialog.layout')}
          </legend>
          <SegmentedControl<Layout>
            value={layout}
            onValueChange={setLayout}
            options={[
              { value: 'video', label: t('exports.dialog.videoOnly') },
              { value: 'video_map', label: t('exports.dialog.videoMap'), disabled: !mapAvailable },
            ]}
          />
          {!mapAvailable && <p className="m-0 text-xs text-fg-3">{t('exports.dialog.noMap')}</p>}
        </fieldset>

        <fieldset className="m-0 flex flex-col gap-2 border-0 p-0">
          <legend className="mb-1.5 text-control font-semibold">
            {t('exports.dialog.quality')}
          </legend>
          <SegmentedControl<Quality>
            value={quality}
            onValueChange={(q) => {
              // E09: 1080p on Free opens the upgrade screen instead.
              if (q === '1080' && !proQuality) {
                onOpenChange(false);
                void navigate({ to: '/settings/$section', params: { section: 'billing' } });
                return;
              }
              setQuality(q);
            }}
            options={[
              { value: '720', label: '720p' },
              { value: '1080', label: opt('1080p', !proQuality) },
            ]}
          />
        </fieldset>

        {watermark && (
          <Banner tone="info">
            {t('exports.dialog.watermark')}{' '}
            <Link
              to="/settings/$section"
              params={{ section: 'billing' }}
              className="font-semibold text-accent underline-offset-2 hover:underline"
              onClick={() => onOpenChange(false)}
            >
              {t('exports.dialog.upgrade')}
            </Link>
          </Banner>
        )}
        {errorMessage && <Banner tone="err">{errorMessage}</Banner>}
      </div>
    </Dialog>
  );
}
