import { useQueryClient } from '@tanstack/react-query';
import { useRouter } from '@tanstack/react-router';
import { useCallback } from 'react';
import { create } from 'zustand';
import type { Membership } from '@/api/identity';
import { useOnboardingDraft } from '@/features/onboarding/draft';
import { signOut, switchOrganization } from './session';

/**
 * Hook point for DLG-03 before signing out (G.E17): the upload manager (M1-FE-07+) reports
 * running uploads here; the top bar asks for confirmation while it is > 0.
 */
export const useSignOutGuard = create<{
  uploadsInProgress: number;
  setUploadsInProgress: (n: number) => void;
}>()((set) => ({
  uploadsInProgress: 0,
  setUploadsInProgress: (n) => set({ uploadsInProgress: n }),
}));

/** Sign out: revoke server-side, drop every cached query (other user's data), go to SCR-02. */
export function useSignOut() {
  const queryClient = useQueryClient();
  const router = useRouter();
  return useCallback(async () => {
    await signOut();
    useOnboardingDraft.getState().reset();
    queryClient.clear();
    await router.navigate({ to: '/signin' });
  }, [queryClient, router]);
}

/**
 * G.E13 Switch organization: new token (org / role / region claims) via refresh `{org_id}`,
 * then "reload" the app in place — cached queries belong to the previous org (and possibly
 * another region), so they are dropped and the library of the new org is opened.
 */
export function useSwitchOrganization() {
  const queryClient = useQueryClient();
  const router = useRouter();
  return useCallback(
    async (membership: Membership): Promise<boolean> => {
      const ok = await switchOrganization(membership.org_id);
      if (!ok) return false;
      queryClient.clear();
      await router.navigate({ to: '/library' });
      await router.invalidate();
      return true;
    },
    [queryClient, router],
  );
}
