import { useResolvedTheme, useSystemThemeListener, useThemeStore } from '@/lib/theme';

const SWATCHES = [
  ['bg', 'bg-bg'],
  ['surface-1', 'bg-surface-1'],
  ['surface-2', 'bg-surface-2'],
  ['surface-3', 'bg-surface-3'],
  ['line', 'bg-line'],
  ['line-strong', 'bg-line-strong'],
  ['accent', 'bg-accent'],
  ['info', 'bg-info'],
  ['warn', 'bg-warn'],
  ['danger', 'bg-danger'],
  ['team-a', 'bg-team-a'],
  ['team-b', 'bg-team-b'],
  ['referee', 'bg-referee'],
  ['ball', 'bg-ball'],
] as const;

/** Temporary token preview (M1-FE-01). Replaced by the router + app shell in M1-FE-03. */
export function App() {
  useSystemThemeListener();
  const theme = useResolvedTheme();
  const toggle = useThemeStore((s) => s.toggle);

  return (
    <main className="mx-auto flex max-w-[1400px] flex-col gap-6 px-8 pt-[26px] pb-[60px]">
      <header className="flex items-center gap-3.5">
        <h1>PitchVision</h1>
        <span className="text-fg-3">design tokens</span>
        <div className="flex-1" />
        <button
          type="button"
          onClick={toggle}
          className="h-[34px] rounded-md border border-line-strong bg-surface-2 px-3.5 text-control font-semibold focus-ring hover:bg-surface-3"
        >
          Theme: {theme}
        </button>
      </header>
      <section className="rounded-2xl border border-line bg-surface-1 px-5 py-[18px]">
        <h2 className="mb-3">Typography</h2>
        <p className="text-fg-2">
          Inter for UI, <span className="font-mono">JetBrains Mono 00:42:17.08</span> for numbers.
        </p>
      </section>
      <section className="grid grid-cols-7 gap-3">
        {SWATCHES.map(([name, cls]) => (
          <div key={name} className="flex flex-col gap-1.5">
            <div className={`${cls} h-12 rounded-lg border border-line`} />
            <span className="text-xs text-fg-3">{name}</span>
          </div>
        ))}
      </section>
    </main>
  );
}
