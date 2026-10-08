import { Braces, Sheet } from 'lucide-react';
import { useTranslation } from 'react-i18next';
import type { Manifest } from '@/api/types';
import { useFormat } from '@/i18n/format';
import { Button, Card } from '@/ui';

/**
 * SCR-20f Data (M3-BE-03): what the raw-data export contains + E01/E02 (open SCR-25 on the Data tab with the format
 * preset). E04–E06 (API access) wait for API keys.
 */
export function DataTab({
  manifest,
  onExport,
}: {
  manifest: Manifest;
  onExport: (f: 'json' | 'csv') => void;
}) {
  const { t } = useTranslation('video');
  const fmt = useFormat();
  return (
    <Card className="grid gap-4">
      <p className="text-body-sm m-0 text-fg-2">
        {t('data.summary', {
          frames: fmt.number(manifest.frame_count),
          duration: fmt.timecode(manifest.duration_ms),
        })}
      </p>
      <ul className="text-body-sm m-0 grid gap-1 pl-5 text-fg-2">
        <li>{t('data.tracking')}</li>
        <li>{t('data.flags')}</li>
        <li>{t('data.stats')}</li>
      </ul>
      <div className="flex flex-wrap gap-2">
        <Button variant="secondary" onClick={() => onExport('json')}>
          <Braces size={15} aria-hidden="true" />
          {t('data.json')}
        </Button>
        <Button variant="secondary" onClick={() => onExport('csv')}>
          <Sheet size={15} aria-hidden="true" />
          {t('data.csv')}
        </Button>
      </div>
    </Card>
  );
}
