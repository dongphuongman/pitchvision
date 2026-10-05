/**
 * Built-in accessible labels used inside primitives (close buttons, spinners…).
 * Single seam so the i18n layer can provide them; components never inline user-facing text.
 */
export interface UiStrings {
  close: string;
  loading: string;
  dismiss: string;
  notifications: string;
}

const EN: UiStrings = {
  close: 'Close',
  loading: 'Loading',
  dismiss: 'Dismiss',
  notifications: 'Notifications',
};

export function useUiStrings(): UiStrings {
  return EN;
}
