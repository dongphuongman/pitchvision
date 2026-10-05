import { useTranslation } from 'react-i18next';
import { Dialog, Kbd } from '@/ui';

/** DLG-05 Keyboard shortcuts (app-wide subset; screen-specific keys are added with each screen). */
export function ShortcutsDialog({
  open,
  onOpenChange,
}: {
  open: boolean;
  onOpenChange: (open: boolean) => void;
}) {
  const { t } = useTranslation();
  const rows = [
    { keys: ['?'], label: t('shortcuts.openShortcuts') },
    { keys: ['N'], label: t('shortcuts.newAnalysis') },
    { keys: ['Esc'], label: t('shortcuts.closeDialog') },
  ];
  return (
    <Dialog open={open} onOpenChange={onOpenChange} title={t('shortcuts.title')} size="sm">
      <dl className="m-0 grid grid-cols-[1fr_auto] gap-x-6 gap-y-3">
        {rows.map((r) => (
          <div key={r.label} className="contents">
            <dt className="text-fg-2">{r.label}</dt>
            <dd className="m-0 flex gap-1">
              {r.keys.map((k) => (
                <Kbd key={k}>{k}</Kbd>
              ))}
            </dd>
          </div>
        ))}
      </dl>
    </Dialog>
  );
}
