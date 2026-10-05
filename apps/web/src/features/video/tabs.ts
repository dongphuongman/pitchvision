/**
 * SCR-20 workspace tabs. Only Overview ships in M1; Players (M2), Heatmaps/Passing/Data (M3),
 * Tactics (M4) are added here when their milestone lands (UIUX §1.4: unreleased = not shown).
 */
export const WORKSPACE_TABS = ['overview'] as const;
export type WorkspaceTab = (typeof WORKSPACE_TABS)[number];
