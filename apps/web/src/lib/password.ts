/** Contract: password ≥ 10 characters (identity `POST /auth/register`, `/password/reset`). */
export const MIN_PASSWORD_LENGTH = 10;

export type PasswordStrength = 0 | 1 | 2 | 3 | 4;

/**
 * Coarse strength score for the meter (UIUX SCR-03 E04). 0 = below the minimum length (not
 * accepted); 1–4 = weak → strong from length and character variety. Advisory only — the
 * server enforces the length rule.
 */
export function passwordStrength(password: string): PasswordStrength {
  if (password.length < MIN_PASSWORD_LENGTH) return 0;
  if (/^(.)\1*$/.test(password)) return 1; // "aaaaaaaaaa"
  const variety = [/[a-z]/, /[A-Z]/, /\d/, /[^A-Za-z0-9]/].filter((r) => r.test(password)).length;
  let score = 1;
  if (password.length >= 14) score += 1;
  if (variety >= 3) score += 1;
  if (password.length >= 18 || (variety === 4 && password.length >= 12)) score += 1;
  return Math.min(score, 4) as PasswordStrength;
}

const EMAIL_RE = /^[^\s@]+@[^\s@]+\.[^\s@]+$/;

export function isValidEmail(email: string): boolean {
  return EMAIL_RE.test(email.trim());
}
