import type { ReactNode } from 'react';
import { cn } from '@/lib/cn';

export interface EmptyStateProps {
  icon?: ReactNode;
  title: ReactNode;
  description?: ReactNode;
  /** One primary CTA (+ optional secondary ones), per UIUX "illustration + 1 sentence + 1 CTA". */
  actions?: ReactNode;
  /** `dashed` = prototype `.empty` (library); `plain` for inside cards / panels. */
  variant?: 'dashed' | 'plain';
  className?: string;
}

export function EmptyState({
  icon,
  title,
  description,
  actions,
  variant = 'dashed',
  className,
}: EmptyStateProps) {
  return (
    <section
      className={cn(
        'grid place-items-center text-center',
        variant === 'dashed'
          ? 'rounded-4xl border-[1.5px] border-dashed border-line-strong bg-surface-1 px-5 py-[90px]'
          : 'px-5 py-12',
        className,
      )}
    >
      {icon && (
        <div
          aria-hidden="true"
          className="grid size-16 place-items-center rounded-4xl bg-[color-mix(in_srgb,var(--ac)_14%,transparent)] text-accent"
        >
          {icon}
        </div>
      )}
      <h2 className="mt-[18px] mb-1.5 text-[22px]">{title}</h2>
      {description && <p className="m-0 mb-[22px] max-w-[52ch] text-fg-2">{description}</p>}
      {actions && (
        <div className="flex flex-wrap items-center justify-center gap-2.5">{actions}</div>
      )}
    </section>
  );
}
