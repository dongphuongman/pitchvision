import { create } from 'zustand';
import { persist } from 'zustand/middleware';

/** UI preferences kept client-side (server-backed profile preferences come later with SCR-40). */
export type UnitSystem = 'metric' | 'imperial';

interface PreferencesState {
  /** Metres by default (UIUX / AD §11: "đơn vị mét/yard"). */
  units: UnitSystem;
  setUnits: (units: UnitSystem) => void;
}

export const usePreferences = create<PreferencesState>()(
  persist(
    (set) => ({
      units: 'metric',
      setUnits: (units) => set({ units }),
    }),
    { name: 'pv-prefs', version: 1 },
  ),
);
