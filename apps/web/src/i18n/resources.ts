import auth from './locales/en/auth.json';
import common from './locales/en/common.json';
import errors from './locales/en/errors.json';
import library from './locales/en/library.json';
import settings from './locales/en/settings.json';
import ui from './locales/en/ui.json';
import video from './locales/en/video.json';

/** English is bundled (default + fallback); other languages are lazy-loaded per namespace. */
export const en = { common, ui, library, video, settings, auth, errors } as const;

export const NAMESPACES = Object.keys(en) as (keyof typeof en)[];
export type Namespace = keyof typeof en;

/** Planned UI languages (PRD: EN first; es, pt, de, fr, vi later). Only `en` ships today. */
export const SUPPORTED_LANGUAGES = ['en', 'es', 'pt', 'de', 'fr', 'vi'] as const;
export type Language = (typeof SUPPORTED_LANGUAGES)[number];
export const DEFAULT_LANGUAGE: Language = 'en';
