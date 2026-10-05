import { useTranslation } from 'react-i18next';

/**
 * Built-in accessible labels used inside primitives (close buttons, spinners…).
 * Single seam to i18n (`ui` namespace); components never inline user-facing text.
 */
export interface UiStrings {
  close: string;
  loading: string;
  dismiss: string;
  notifications: string;
  showPassword: string;
  hidePassword: string;
}

export function useUiStrings(): UiStrings {
  const { t } = useTranslation('ui');
  return {
    close: t('close'),
    loading: t('loading'),
    dismiss: t('dismiss'),
    notifications: t('notifications'),
    showPassword: t('showPassword'),
    hidePassword: t('hidePassword'),
  };
}
