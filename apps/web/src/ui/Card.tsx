import type { HTMLAttributes, ReactNode } from 'react';
import { cn } from '@/lib/cn';

export interface CardProps extends HTMLAttributes<HTMLDivElement> {
  /** Prototype `.card.p` padding (18/20). */
  padded?: boolean;
  /** Interactive hover lift (prototype `.vcard:hover`). */
  interactive?: boolean;
}

/** Prototype `.card`: surface-1, hairline border, radius 14. */
export function Card({ padded = true, interactive, className, ...rest }: CardProps) {
  return (
    <div
      className={cn(
        'rounded-2xl border border-line bg-surface-1',
        padded && 'px-5 py-[18px]',
        interactive &&
          'transition-[border-color,transform] duration-[120ms] hover:-translate-y-px hover:border-line-strong',
        className,
      )}
      {...rest}
    />
  );
}

export interface CardTitleProps extends HTMLAttributes<HTMLHeadingElement> {
  actions?: ReactNode;
}

/** Prototype `.ct`: small uppercase section title inside a card. */
export function CardTitle({ actions, className, children, ...rest }: CardTitleProps) {
  return (
    <div className="mb-3 flex items-center gap-2.5">
      <h3
        className={cn('flex-1 text-sm font-bold tracking-[.06em] text-fg-2 uppercase', className)}
        {...rest}
      >
        {children}
      </h3>
      {actions}
    </div>
  );
}
