/**
 * SCR-20 workspace tabs: Overview (M1, stats M3), Heatmaps, Passing and Data (M3). Players (M2), Passing/Data (M3) and
 * Tactics (M4) are added here when they land (UIUX §1.4: unreleased = not shown).
 */
export const WORKSPACE_TABS = ['overview', 'heatmaps', 'passing', 'data'] as const;
export type WorkspaceTab = (typeof WORKSPACE_TABS)[number];
