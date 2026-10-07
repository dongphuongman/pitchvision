import { useQuery, useQueryClient } from '@tanstack/react-query';
import { useCallback } from 'react';
import { identityApi, ME_KEY } from '@/api/identity';

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

/**
 * E10 "Save as my default" / SCR-40 R02: the user's defaults live on the account (identity `Me`
 * `analysis_defaults`, null = product defaults), so they follow the user across browsers.
 */
export function useAnalysisDefaults(): {
  settings: AnalysisSettings;
  loaded: boolean;
  save: (s: AnalysisSettings | null) => Promise<void>;
} {
  const qc = useQueryClient();
  const me = useQuery({ queryKey: ME_KEY, queryFn: ({ signal }) => identityApi.me(signal) });
  const save = useCallback(
    async (s: AnalysisSettings | null) => {
      qc.setQueryData(ME_KEY, await identityApi.updateMe({ analysis_defaults: s }));
    },
    [qc],
  );
  return {
    settings: me.data?.analysis_defaults ?? DEFAULT_SETTINGS,
    loaded: !me.isPending,
    save,
  };
}
