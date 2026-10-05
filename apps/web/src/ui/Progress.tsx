import { Progress as RadixProgress } from 'radix-ui';
import { cn } from '@/lib/cn';

export interface ProgressProps {
  /** 0–100; `null` renders an indeterminate bar. */
  value: number | null;
  /** Accessible name, e.g. "Upload progress". */
  label: string;
  tone?: 'accent' | 'info' | 'warn' | 'danger';
  size?: 'xs' | 'sm' | 'md';
  className?: string;
}

const TONE = { accent: 'bg-accent', info: 'bg-info', warn: 'bg-warn', danger: 'bg-danger' };
const SIZE = { xs: 'h-1 rounded-sm', sm: 'h-1.5 rounded-sm', md: 'h-2 rounded' };

/** Prototype `.pbar` (8px, line-strong track). */
export function Progress({ value, label, tone = 'accent', size = 'md', className }: ProgressProps) {
  const clamped = value === null ? null : Math.min(100, Math.max(0, value));
  return (
    <RadixProgress.Root
      value={clamped}
      max={100}
      aria-label={label}
      className={cn('relative w-full overflow-hidden bg-line-strong', SIZE[size], className)}
    >
      <RadixProgress.Indicator
        className={cn(
          'h-full rounded-[inherit] transition-[width] duration-300',
          TONE[tone],
          clamped === null && 'w-1/3 animate-blink',
        )}
        style={clamped === null ? undefined : { width: `${clamped}%` }}
      />
    </RadixProgress.Root>
  );
}
