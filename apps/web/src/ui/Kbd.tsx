import type { HTMLAttributes } from 'react';
import { cn } from '@/lib/cn';

/** Prototype `.kbd`: mono key cap with a 2px bottom border. */
export function Kbd({ className, ...rest }: HTMLAttributes<HTMLElement>) {
  return (
    <kbd
      className={cn(
        'inline-block rounded-xs border border-b-2 border-line-strong bg-surface-2 px-1.5 py-px font-mono text-2xs leading-[1.4] text-fg-2',
        className,
      )}
      {...rest}
    />
  );
}
