import 'i18next';
import type { en } from './resources';

// Type-safe translation keys: t('nav.library') is checked against the EN catalogue.
declare module 'i18next' {
  interface CustomTypeOptions {
    defaultNS: 'common';
    resources: typeof en;
    returnNull: false;
  }
}
