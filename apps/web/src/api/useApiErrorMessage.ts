import { useTranslation } from 'react-i18next';
import { isApiError } from './errors';

/**
 * User-facing message for an error: localised by `code` (errors namespace), never the raw
 * server `detail` (which is English and for developers / logs).
 */
export function useApiErrorMessage(error: unknown): string | undefined {
  const { t } = useTranslation('errors');
  if (error === null || error === undefined) return undefined;
  return isApiError(error) ? t(`codes.${error.code}`) : t('codes.UNKNOWN');
}
