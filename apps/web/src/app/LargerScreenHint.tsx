import { useTranslation } from 'react-i18next';
import { Banner } from '@/ui';

/** UIUX §09: editing screens (SCR-14 / 15) on a phone — still usable, but say so (< 768 px only). */
export function LargerScreenHint() {
  const { t } = useTranslation();
  return (
    <div className="md:hidden">
      <Banner tone="info">{t('largerScreen')}</Banner>
    </div>
  );
}
