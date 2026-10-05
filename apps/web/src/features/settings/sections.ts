/** SCR-40 sections (UIUX) + `about` (build / API / contract versions). */
export const SETTINGS_SECTIONS = [
  'profile',
  'organization',
  'members',
  'billing',
  'api-keys',
  'preferences',
  'privacy',
  'about',
] as const;
export type SettingsSection = (typeof SETTINGS_SECTIONS)[number];
