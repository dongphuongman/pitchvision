import { useEffect } from 'react';
import { create } from 'zustand';

export type ThemePreference = 'system' | 'light' | 'dark';
export type ResolvedTheme = 'light' | 'dark';

export const THEME_STORAGE_KEY = 'pv-theme';

function readStored(): ThemePreference {
  try {
    const v = localStorage.getItem(THEME_STORAGE_KEY);
    return v === 'light' || v === 'dark' ? v : 'system';
  } catch {
    return 'system';
  }
}

function systemTheme(): ResolvedTheme {
  if (typeof window === 'undefined' || typeof window.matchMedia !== 'function') return 'dark';
  return window.matchMedia('(prefers-color-scheme: light)').matches ? 'light' : 'dark';
}

/** Writes the explicit choice to `<html data-theme>`; "system" removes it so the media query wins. */
export function applyTheme(pref: ThemePreference): void {
  const root = document.documentElement;
  if (pref === 'system') root.removeAttribute('data-theme');
  else root.setAttribute('data-theme', pref);
  try {
    if (pref === 'system') localStorage.removeItem(THEME_STORAGE_KEY);
    else localStorage.setItem(THEME_STORAGE_KEY, pref);
  } catch {
    /* storage unavailable (private mode) — attribute alone is enough for this session */
  }
}

interface ThemeState {
  preference: ThemePreference;
  system: ResolvedTheme;
  setPreference: (pref: ThemePreference) => void;
  /** Flip between light and dark, starting from what is currently shown. */
  toggle: () => void;
}

export const useThemeStore = create<ThemeState>()((set, get) => ({
  preference: readStored(),
  system: systemTheme(),
  setPreference: (preference) => {
    applyTheme(preference);
    set({ preference });
  },
  toggle: () => {
    const { preference, system } = get();
    const shown = preference === 'system' ? system : preference;
    get().setPreference(shown === 'dark' ? 'light' : 'dark');
  },
}));

export function resolveTheme(pref: ThemePreference, system: ResolvedTheme): ResolvedTheme {
  return pref === 'system' ? system : pref;
}

export function useResolvedTheme(): ResolvedTheme {
  const preference = useThemeStore((s) => s.preference);
  const system = useThemeStore((s) => s.system);
  return resolveTheme(preference, system);
}

/** Mount once near the root: tracks OS theme changes while preference is "system". */
export function useSystemThemeListener(): void {
  useEffect(() => {
    if (typeof window.matchMedia !== 'function') return;
    const mq = window.matchMedia('(prefers-color-scheme: light)');
    const onChange = () => useThemeStore.setState({ system: mq.matches ? 'light' : 'dark' });
    mq.addEventListener('change', onChange);
    return () => mq.removeEventListener('change', onChange);
  }, []);
}
