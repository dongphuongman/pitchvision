import type { VariantProps } from 'class-variance-authority';
import { Slot } from 'radix-ui';
import { forwardRef, type ButtonHTMLAttributes, type ReactNode } from 'react';
import { cn } from '@/lib/cn';
import { buttonVariants } from './classes';
import { Spinner } from './Spinner';

export interface ButtonProps
  extends ButtonHTMLAttributes<HTMLButtonElement>, VariantProps<typeof buttonVariants> {
  /** Shows a spinner, sets aria-busy and blocks clicks while keeping the label for layout. */
  loading?: boolean;
  /** Render the child element (e.g. a router Link) with button styling. */
  asChild?: boolean;
  leadingIcon?: ReactNode;
}

export const Button = forwardRef<HTMLButtonElement, ButtonProps>(function Button(
  {
    variant,
    size,
    loading = false,
    asChild = false,
    leadingIcon,
    className,
    disabled,
    children,
    type,
    ...rest
  },
  ref,
) {
  const classes = cn(buttonVariants({ variant, size }), className);
  if (asChild) {
    return (
      <Slot.Root ref={ref} className={classes} {...rest}>
        {children}
      </Slot.Root>
    );
  }
  return (
    <button
      ref={ref}
      type={type ?? 'button'}
      className={classes}
      disabled={disabled || loading}
      aria-busy={loading || undefined}
      {...rest}
    >
      {loading ? <Spinner size={size === 'lg' ? 18 : 15} label={null} /> : leadingIcon}
      {children}
    </button>
  );
});
