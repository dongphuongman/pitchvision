import type { HTMLAttributes } from 'react';
import { cn } from '@/lib/cn';

/** Loading placeholder (UIUX §2.3 "Skeleton khi tải"). Decorative: wrap regions in aria-busy. */
export function Skeleton({ className, ...rest }: HTMLAttributes<HTMLDivElement>) {
  return (
    <div
      aria-hidden="true"
      className={cn('animate-blink rounded-md bg-surface-3', className)}
      {...rest}
    />
  );
}
