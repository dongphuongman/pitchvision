import { create } from 'zustand';
import { createJSONStorage, persist } from 'zustand/middleware';
import type { OrgKind, Region } from '@/api/identity';

export const ONBOARDING_STEPS = ['organization', 'region', 'preferences'] as const;
export type OnboardingStep = (typeof ONBOARDING_STEPS)[number];

/**
 * SCR-07 wizard draft. Kept in sessionStorage so a reload mid-wizard keeps the typed values
 * ("Back keeps entered data"); cleared on finish / sign-out.
 * The org is created when step 2 is confirmed (the region is required and immutable), after
 * which steps 1–2 become read-only except the name (PATCH /orgs/{id}).
 */
export interface OnboardingDraft {
  name: string;
  kind: OrgKind;
  roleTitle: string;
  region: Region | null;
  createdOrgId: string | null;
  set: (patch: Partial<Omit<OnboardingDraft, 'set' | 'reset'>>) => void;
  reset: () => void;
}

const EMPTY = {
  name: '',
  kind: 'club' as OrgKind,
  roleTitle: '',
  region: null,
  createdOrgId: null,
};

export const useOnboardingDraft = create<OnboardingDraft>()(
  persist(
    (set) => ({
      ...EMPTY,
      set: (patch) => set(patch),
      reset: () => set({ ...EMPTY }),
    }),
    {
      name: 'pv-onboarding',
      version: 1,
      storage: createJSONStorage(() => sessionStorage),
      partialize: ({ name, kind, roleTitle, region, createdOrgId }) => ({
        name,
        kind,
        roleTitle,
        region,
        createdOrgId,
      }),
    },
  ),
);
