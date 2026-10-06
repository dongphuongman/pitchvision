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

/** Minimum role per section (UIUX SCR-40 E01–E07: hidden when not allowed). */
export const SECTION_ROLE: Record<SettingsSection, 'viewer' | 'admin' | 'owner'> = {
  profile: 'viewer',
  organization: 'admin',
  members: 'admin',
  billing: 'owner',
  'api-keys': 'admin',
  preferences: 'viewer',
  privacy: 'viewer',
  about: 'viewer',
};
