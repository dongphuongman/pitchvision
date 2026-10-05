import type { VariantProps } from 'class-variance-authority';
import type { HTMLAttributes, ReactNode } from 'react';
import { cn } from '@/lib/cn';
import { badgeVariants } from './classes';

export interface BadgeProps
  extends HTMLAttributes<HTMLSpanElement>, VariantProps<typeof badgeVariants> {
  icon?: ReactNode;
  /** Animated live dot (prototype `.pulse`). */
  pulse?: boolean;
}

export function Badge({ tone, size, icon, pulse, className, children, ...rest }: BadgeProps) {
  return (
    <span className={cn(badgeVariants({ tone, size }), className)} {...rest}>
      {pulse && (
        <span aria-hidden="true" className="size-2 animate-pulse-dot rounded-full bg-danger" />
      )}
      {icon && (
        <span aria-hidden="true" className="flex">
          {icon}
        </span>
      )}
      {children}
    </span>
  );
}
