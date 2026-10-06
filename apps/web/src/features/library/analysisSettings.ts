import { create } from 'zustand';
import { persist } from 'zustand/middleware';

/**
 * SCR-12 Analysis settings (M1 scope): mode + detection confidence. Format is 11v11 only in M1
 * (9v9/7v7 → Phase C), modules ship from M2, Compute is self-host only — none are shown yet.
 */
export type AnalysisMode = 'standard' | 'fast';

export interface AnalysisSettings {
  mode: AnalysisMode;
  /** PipelineConfig.conf_threshold */
  confidence: number;
}

export const DEFAULT_SETTINGS: AnalysisSettings = { mode: 'standard', confidence: 0.35 };
export const CONFIDENCE_RANGE = { min: 0.2, max: 0.8, step: 0.05 } as const;

/** Partial PipelineConfig for `POST /jobs {config}` — only what differs from the server defaults. */
export function toJobConfig(s: AnalysisSettings): Record<string, unknown> | undefined {
  const config: Record<string, unknown> = {};
  if (s.mode === 'fast') config.frame_stride = 2;
  if (Math.abs(s.confidence - DEFAULT_SETTINGS.confidence) > 1e-9)
    config.conf_threshold = s.confidence;
  return Object.keys(config).length ? config : undefined;
}

/** E10 "Save as my default" (client-side until server-backed preferences, SCR-40). */
export const useAnalysisDefaults = create<{
  settings: AnalysisSettings;
  save: (s: AnalysisSettings) => void;
}>()(
  persist((set) => ({ settings: DEFAULT_SETTINGS, save: (settings) => set({ settings }) }), {
    name: 'pv-analysis-defaults',
    version: 1,
  }),
);
