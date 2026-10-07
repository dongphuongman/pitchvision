import { useTranslation } from 'react-i18next';
import { changeLanguage } from '@/i18n';
import { useFormat } from '@/i18n/format';
import { SUPPORTED_LANGUAGES, type Language } from '@/i18n/resources';
import { usePreferences, type UnitSystem } from '@/lib/preferences';
import { useThemeStore, type ThemePreference } from '@/lib/theme';
import { Button, Card, Field, Select } from '@/ui';
import { AdvancedSettingsDialog } from '@/features/library/AdvancedSettingsDialog';
import { useAnalysisDefaults } from '@/features/library/analysisSettings';
import { useState } from 'react';
import { EmailNotificationsCard } from './EmailNotificationsCard';

/** Only languages with a shipped catalogue are selectable; the rest appear once translated. */
const AVAILABLE: readonly Language[] = SUPPORTED_LANGUAGES.filter((l) => l === 'en');

export function PreferencesSection() {
  const { t, i18n } = useTranslation('settings');
  const { t: tc } = useTranslation();
  const theme = useThemeStore((s) => s.preference);
  const setTheme = useThemeStore((s) => s.setPreference);
  const units = usePreferences((s) => s.units);
  const setUnits = usePreferences((s) => s.setUnits);
  const fmt = useFormat();
  const languageName = new Intl.DisplayNames([i18n.language], { type: 'language' });

  return (
    <>
      <Card className="grid gap-4 sm:grid-cols-2">
        <Field label={t('preferences.theme')}>
          <Select
            value={theme}
            onValueChange={(v) => setTheme(v as ThemePreference)}
            options={(['system', 'dark', 'light'] as const).map((v) => ({
              value: v,
              label: tc(`theme.${v}`),
            }))}
          />
        </Field>
        <Field label={t('preferences.language')}>
          <Select
            value={i18n.language}
            onValueChange={(v) => void changeLanguage(v as Language)}
            options={AVAILABLE.map((l) => ({ value: l, label: languageName.of(l) ?? l }))}
          />
        </Field>
        <Field
          label={t('preferences.units')}
          help={t('preferences.unitsHelp', { example: fmt.distance(105) })}
        >
          <Select
            value={units}
            onValueChange={(v) => setUnits(v as UnitSystem)}
            options={(['metric', 'imperial'] as const).map((v) => ({
              value: v,
              label: tc(`units.${v}`),
            }))}
          />
        </Field>
      </Card>
      <AnalysisDefaultsCard />
      <EmailNotificationsCard />
    </>
  );
}

/** R02 Default analysis settings · Edit → SCR-12 in "defaults" mode (saved on the account). */
function AnalysisDefaultsCard() {
  const { t } = useTranslation('settings');
  const { t: tl } = useTranslation('library');
  const { settings, loaded } = useAnalysisDefaults();
  const [open, setOpen] = useState(false);
  return (
    <Card className="flex flex-wrap items-center justify-between gap-3">
      <div className="flex flex-col gap-1">
        <b>{t('preferences.analysisDefaults')}</b>
        <span className="text-fg-2">
          {loaded
            ? t('preferences.analysisSummary', {
                mode: tl(`settings.${settings.mode}`),
                confidence: settings.confidence.toFixed(2),
              })
            : '…'}
        </span>
      </div>
      <Button variant="ghost" disabled={!loaded} onClick={() => setOpen(true)}>
        {t('preferences.edit')}
      </Button>
      <AdvancedSettingsDialog
        open={open}
        onOpenChange={setOpen}
        value={settings}
        onApply={() => undefined}
        mode="defaults"
      />
    </Card>
  );
}
