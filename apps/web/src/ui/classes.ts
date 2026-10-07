import { cva, type VariantProps } from 'class-variance-authority';
import { cn } from '@/lib/cn';

// Shared class recipes (kept out of component files so Fast Refresh stays component-only).

/** Mirrors prototype `.btn` (+ `.primary/.ghost/.danger/.solid`, `.sm/.lg`). */
export const buttonVariants = cva(
  [
    'inline-flex shrink-0 items-center justify-center gap-2 border font-semibold whitespace-nowrap focus-ring',
    'transition-[background-color,border-color,color,filter] duration-[120ms] select-none',
    'disabled:pointer-events-none disabled:opacity-45 aria-disabled:pointer-events-none aria-disabled:opacity-45',
  ],
  {
    variants: {
      variant: {
        primary: 'border-transparent bg-accent text-accent-ink hover:brightness-107',
        secondary: 'border-line-strong bg-surface-2 text-fg hover:bg-surface-3',
        ghost: 'border-transparent bg-transparent text-fg-2 hover:bg-surface-3 hover:text-fg',
        danger:
          'border-[color-mix(in_srgb,var(--dan)_40%,transparent)] bg-transparent text-danger hover:bg-[color-mix(in_srgb,var(--dan)_10%,transparent)]',
        'danger-solid': 'border-transparent bg-danger text-white hover:brightness-107',
      },
      size: {
        sm: 'h-[30px] rounded-md px-[11px] text-sm',
        md: 'h-[34px] rounded-md px-3.5 text-control',
        lg: 'h-11 rounded-lg px-5 text-md',
      },
    },
    defaultVariants: { variant: 'secondary', size: 'md' },
  },
);

/** Prototype `.badge` + `.b-ok/.b-run/.b-warn/.b-err/.b-mute/.b-pro`. */
export const badgeVariants = cva(
  'inline-flex items-center gap-1.5 rounded-full font-[650] whitespace-nowrap',
  {
    variants: {
      tone: {
        ok: 'bg-[color-mix(in_srgb,var(--ac)_14%,transparent)] text-accent',
        run: 'bg-[color-mix(in_srgb,var(--info)_14%,transparent)] text-info',
        warn: 'bg-[color-mix(in_srgb,var(--warn)_14%,transparent)] text-warn',
        err: 'bg-[color-mix(in_srgb,var(--dan)_14%,transparent)] text-danger',
        mute: 'bg-surface-3 text-fg-2',
        pro: 'bg-[color-mix(in_srgb,var(--pro)_15%,transparent)] tracking-[.04em] text-pro',
      },
      size: {
        sm: 'px-[7px] py-0.5 text-[10.5px]',
        md: 'px-[9px] py-[3px] text-xs',
      },
    },
    defaultVariants: { tone: 'mute', size: 'md' },
  },
);

export type BadgeTone = NonNullable<VariantProps<typeof badgeVariants>['tone']>;

/** Prototype `.input` (+ `.focus` ring, `.err`). Exported for Select's trigger. */
export const controlClasses = cn(
  'w-full rounded-md border border-line-strong bg-bg text-base text-fg outline-none',
  'transition-[border-color,box-shadow] duration-[120ms] placeholder:text-fg-3',
  'focus-visible:border-accent focus-visible:shadow-[0_0_0_3px_color-mix(in_srgb,var(--ac)_22%,transparent)]',
  'disabled:cursor-not-allowed disabled:opacity-55 aria-invalid:border-danger',
);

/** Prototype `.menu` surface; shared with Select. */
export const menuContentClasses =
  'z-[85] min-w-[220px] overflow-auto rounded-xl border border-line-strong bg-surface-1 p-1.5 text-fg shadow-pop';

/** < 768 px: a menu is a bottom sheet (UIUX §09). The popper wrapper is pinned to the bottom edge in
 * styles/index.css (`[data-pv-sheet]`); these restyle the panel: full width, top corners only, a grabber,
 * 70 % of the screen at most, finger-sized rows, clear of the home indicator. */
export const sheetClasses = cn(
  'max-md:relative max-md:max-h-[70dvh] max-md:w-screen max-md:max-w-none max-md:rounded-none max-md:rounded-t-2xl max-md:border-x-0 max-md:border-b-0',
  'max-md:px-2 max-md:pt-5 max-md:pb-[max(12px,env(safe-area-inset-bottom))]',
  'max-md:before:absolute max-md:before:top-2 max-md:before:left-1/2 max-md:before:h-1 max-md:before:w-9 max-md:before:-translate-x-1/2 max-md:before:rounded-full max-md:before:bg-line-strong',
  'max-md:[&_[role=menuitem]]:min-h-12 max-md:[&_[role=menuitem]]:text-base',
);

/** Prototype `.menu a`. Highlight follows keyboard focus (data-highlighted). */
export const menuItemClasses = cn(
  'relative flex cursor-default items-center gap-2.5 rounded-sm px-2.5 py-2 text-control text-fg outline-none select-none',
  'data-[disabled]:pointer-events-none data-[disabled]:opacity-45 data-[highlighted]:bg-surface-3',
  '[&_svg]:shrink-0',
);
