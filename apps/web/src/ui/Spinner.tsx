import { cn } from '@/lib/cn';
import { useUiStrings } from './strings';

export interface SpinnerProps {
  size?: number;
  className?: string;
  /** Accessible label; pass `null` when a parent already announces the busy state. */
  label?: string | null;
}

export function Spinner({ size = 16, className, label }: SpinnerProps) {
  const strings = useUiStrings();
  const a11yLabel = label === undefined ? strings.loading : label;
  return (
    <svg
      width={size}
      height={size}
      viewBox="0 0 24 24"
      fill="none"
      className={cn('flex-none animate-spin', className)}
      role={a11yLabel ? 'status' : undefined}
      aria-label={a11yLabel ?? undefined}
      aria-hidden={a11yLabel ? undefined : true}
    >
      <circle cx="12" cy="12" r="9" stroke="currentColor" strokeOpacity=".25" strokeWidth="3" />
      <path d="M21 12a9 9 0 0 0-9-9" stroke="currentColor" strokeWidth="3" strokeLinecap="round" />
    </svg>
  );
}
