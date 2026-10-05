import i18next, { type BackendModule } from 'i18next';
import { initReactI18next } from 'react-i18next';
import { DEFAULT_LANGUAGE, en, NAMESPACES, SUPPORTED_LANGUAGES, type Language } from './resources';

// Non-English catalogues: src/i18n/locales/<lng>/<ns>.json, code-split per file.
const lazyCatalogues = import.meta.glob<{ default: Record<string, unknown> }>([
  './locales/*/*.json',
  '!./locales/en/*.json',
]);

const lazyBackend: BackendModule = {
  type: 'backend',
  init: () => undefined,
  read(language, namespace, callback) {
    const load = lazyCatalogues[`./locales/${language}/${namespace}.json`];
    if (!load) {
      callback(null, {}); // missing → i18next falls back to English
      return;
    }
    load().then(
      (mod) => callback(null, mod.default),
      (err: unknown) => callback(err instanceof Error ? err : new Error(String(err)), null),
    );
  },
};

export const LANGUAGE_STORAGE_KEY = 'pv-lang';

function initialLanguage(): Language {
  try {
    const stored = localStorage.getItem(LANGUAGE_STORAGE_KEY);
    if (stored && (SUPPORTED_LANGUAGES as readonly string[]).includes(stored)) {
      return stored as Language;
    }
  } catch {
    /* ignore */
  }
  return DEFAULT_LANGUAGE;
}

void i18next
  .use(lazyBackend)
  .use(initReactI18next)
  .init({
    lng: initialLanguage(),
    fallbackLng: DEFAULT_LANGUAGE,
    supportedLngs: [...SUPPORTED_LANGUAGES],
    ns: NAMESPACES,
    defaultNS: 'common',
    resources: { en },
    partialBundledLanguages: true,
    interpolation: { escapeValue: false }, // React escapes
    returnNull: false,
    react: { useSuspense: false },
    initAsync: false,
  });

i18next.on('languageChanged', (lng) => {
  document.documentElement.lang = lng;
});
document.documentElement.lang = i18next.language;

export async function changeLanguage(lng: Language): Promise<void> {
  try {
    localStorage.setItem(LANGUAGE_STORAGE_KEY, lng);
  } catch {
    /* ignore */
  }
  await i18next.changeLanguage(lng);
}

export { i18next as i18n };
