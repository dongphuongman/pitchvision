import type { ReactNode } from 'react';
import { cn } from '@/lib/cn';

/** Prototype `.page`: centred content column, max 1400px. */
export function Page({ children, className }: { children: ReactNode; className?: string }) {
  return (
    <div className={cn('mx-auto max-w-[1400px] px-4 pt-[26px] pb-[60px] sm:px-8', className)}>
      {children}
    </div>
  );
}

/** Prototype `.ph`: h1 + meta + right-aligned actions. */
export function PageHeader({
  title,
  meta,
  actions,
}: {
  title: ReactNode;
  meta?: ReactNode;
  actions?: ReactNode;
}) {
  return (
    <div className="mb-5 flex flex-wrap items-center gap-x-3.5 gap-y-2">
      <h1 className="min-w-0 break-words">{title}</h1>
      {meta && <span className="text-fg-3">{meta}</span>}
      <div className="flex-1" />
      {actions}
    </div>
  );
}
