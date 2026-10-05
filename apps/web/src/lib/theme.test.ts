import { applyTheme, resolveTheme, THEME_STORAGE_KEY, useThemeStore } from './theme';

describe('theme', () => {
  it('applies an explicit theme to <html data-theme> and persists it', () => {
    applyTheme('light');
    expect(document.documentElement.dataset.theme).toBe('light');
    expect(localStorage.getItem(THEME_STORAGE_KEY)).toBe('light');
  });

  it('"system" clears the attribute so prefers-color-scheme decides', () => {
    applyTheme('dark');
    applyTheme('system');
    expect(document.documentElement.hasAttribute('data-theme')).toBe(false);
    expect(localStorage.getItem(THEME_STORAGE_KEY)).toBeNull();
  });

  it('toggle flips from the currently shown theme', () => {
    useThemeStore.setState({ preference: 'system', system: 'dark' });
    useThemeStore.getState().toggle();
    expect(useThemeStore.getState().preference).toBe('light');
    useThemeStore.getState().toggle();
    expect(useThemeStore.getState().preference).toBe('dark');
  });

  it('resolves system preference', () => {
    expect(resolveTheme('system', 'light')).toBe('light');
    expect(resolveTheme('dark', 'light')).toBe('dark');
  });
});
