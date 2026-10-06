import { useState } from 'react';
import { useTranslation } from 'react-i18next';
import { Button, Checkbox, Dialog, SegmentedControl } from '@/ui';
import {
  CONFIDENCE_RANGE,
  DEFAULT_SETTINGS,
  useAnalysisDefaults,
  type AnalysisMode,
  type AnalysisSettings,
} from './analysisSettings';

interface Props {
  open: boolean;
  onOpenChange: (open: boolean) => void;
  value: AnalysisSettings;
  onApply: (s: AnalysisSettings) => void;
}

/** SCR-12 Advanced settings (modal md, over SCR-11) — M1-FE-10. Mounted only while open so the
 *  draft starts from the current value every time (E01/E12 close without saving). */
export function AdvancedSettingsDialog(props: Props) {
  return props.open ? <SettingsDialog {...props} /> : null;
}

function SettingsDialog({ open, onOpenChange, value, onApply }: Props) {
  const { t } = useTranslation('library');
  const { t: tc } = useTranslation();
  const saveDefault = useAnalysisDefaults((s) => s.save);
  const [draft, setDraft] = useState(value);
  const [asDefault, setAsDefault] = useState(false);

  const apply = () => {
    if (asDefault) saveDefault(draft);
    onApply(draft);
    onOpenChange(false);
  };

  return (
    <Dialog
      open={open}
      onOpenChange={onOpenChange}
      title={t('settings.title')}
      footer={
        <>
          <Button variant="ghost" className="mr-auto" onClick={() => setDraft(DEFAULT_SETTINGS)}>
            {t('settings.reset')}
          </Button>
          <Button onClick={() => onOpenChange(false)}>{tc('actions.cancel')}</Button>
          <Button variant="primary" onClick={apply}>
            {t('settings.apply')}
          </Button>
        </>
      }
    >
      <div className="flex flex-col gap-5">
        <div className="grid gap-1.5">
          <span id="pv-mode" className="text-caption font-semibold text-fg-2">
            {t('settings.mode')}
          </span>
          <SegmentedControl<AnalysisMode>
            aria-labelledby="pv-mode"
            value={draft.mode}
            onValueChange={(mode) => setDraft((d) => ({ ...d, mode }))}
            options={[
              { value: 'standard', label: t('settings.standard') },
              { value: 'fast', label: t('settings.fast') },
            ]}
          />
          <p className="m-0 text-caption text-fg-3">{t(`settings.${draft.mode}Help`)}</p>
        </div>
        <label className="grid gap-1.5">
          <span className="flex justify-between text-caption font-semibold text-fg-2">
            {t('settings.confidence')}
            <span className="font-mono text-fg">{draft.confidence.toFixed(2)}</span>
          </span>
          <input
            type="range"
            className="accent-[var(--accent)]"
            min={CONFIDENCE_RANGE.min}
            max={CONFIDENCE_RANGE.max}
            step={CONFIDENCE_RANGE.step}
            value={draft.confidence}
            onChange={(e) => setDraft((d) => ({ ...d, confidence: Number(e.target.value) }))}
          />
          <span className="text-caption text-fg-3">{t('settings.confidenceHelp')}</span>
        </label>
        <Checkbox
          label={t('settings.saveDefault')}
          checked={asDefault}
          onCheckedChange={(c) => setAsDefault(c === true)}
        />
      </div>
    </Dialog>
  );
}
