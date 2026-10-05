import { cva, type VariantProps } from 'class-variance-authority';
import { forwardRef, type ButtonHTMLAttributes, type ReactNode } from 'react';
import { cn } from '@/lib/cn';
import { Tooltip } from './Tooltip';

/** Prototype `.iconbtn` (ghost) and `.btn.icon` (secondary / primary). */
const iconButtonVariants = cva(
  'relative inline-grid shrink-0 place-items-center rounded-md border focus-ring transition-colors duration-[120ms] disabled:pointer-events-none disabled:opacity-45',
  {
    variants: {
      variant: {
        ghost: 'border-transparent bg-transparent text-fg-2 hover:bg-surface-3 hover:text-fg',
        secondary: 'border-line-strong bg-surface-2 text-fg hover:bg-surface-3',
        primary: 'border-transparent bg-accent text-accent-ink hover:brightness-107',
      },
      size: { sm: 'size-[30px]', md: 'size-[34px]' },
    },
    defaultVariants: { variant: 'ghost', size: 'md' },
  },
);

export interface IconButtonProps
  extends
    Omit<ButtonHTMLAttributes<HTMLButtonElement>, 'children' | 'aria-label'>,
    VariantProps<typeof iconButtonVariants> {
  /** Required accessible name; also used as tooltip text unless `tooltip={false}`. */
  label: string;
  icon: ReactNode;
  tooltip?: boolean;
  /** Small red counter (prototype `.iconbtn .dot`), e.g. unread notifications. */
  badge?: number;
}

export const IconButton = forwardRef<HTMLButtonElement, IconButtonProps>(function IconButton(
  { label, icon, tooltip = true, badge, variant, size, className, type, ...rest },
  ref,
) {
  const button = (
    <button
      ref={ref}
      type={type ?? 'button'}
      aria-label={label}
      className={cn(iconButtonVariants({ variant, size }), className)}
      {...rest}
    >
      <span aria-hidden="true" className="contents">
        {icon}
      </span>
      {badge !== undefined && badge > 0 && (
        <span
          aria-hidden="true"
          className="absolute top-[3px] right-0.5 grid h-4 min-w-4 place-items-center rounded-lg bg-danger px-1 text-[10px] font-bold text-white"
        >
          {badge > 99 ? '99+' : badge}
        </span>
      )}
    </button>
  );
  return tooltip ? <Tooltip content={label}>{button}</Tooltip> : button;
});
