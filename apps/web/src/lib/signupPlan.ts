/**
 * The plan picked on the landing / sign-up (`?plan=pro`) survives email verification and onboarding
 * in sessionStorage; onboarding's finish opens SCR-41 for Pro (UIUX F1 E12 "plan=pro?").
 */
const KEY = 'pv-signup-plan';

export function rememberSignupPlan(plan: string | undefined): void {
  try {
    if (plan === 'pro' || plan === 'team') sessionStorage.setItem(KEY, plan);
    else sessionStorage.removeItem(KEY);
  } catch {
    /* storage unavailable: the user can still upgrade from the meter */
  }
}

/** Reads and clears it. */
export function takeSignupPlan(): 'pro' | 'team' | null {
  try {
    const v = sessionStorage.getItem(KEY);
    sessionStorage.removeItem(KEY);
    return v === 'pro' || v === 'team' ? v : null;
  } catch {
    return null;
  }
}
