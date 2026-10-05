import { Tabs as RadixTabs } from 'radix-ui';
import {
  createContext,
  forwardRef,
  useContext,
  type ComponentPropsWithoutRef,
  type ReactNode,
} from 'react';
import { cn } from '@/lib/cn';

type TabsVariant = 'underline' | 'segmented';
const VariantCtx = createContext<TabsVariant>('underline');

export interface TabsProps extends ComponentPropsWithoutRef<typeof RadixTabs.Root> {
  /** `underline` = prototype `.tabs`; `segmented` = prototype `.seg`. */
  variant?: TabsVariant;
}

export function Tabs({ variant = 'underline', ...rest }: TabsProps) {
  return (
    <VariantCtx.Provider value={variant}>
      <RadixTabs.Root {...rest} />
    </VariantCtx.Provider>
  );
}

export const TabsList = forwardRef<HTMLDivElement, ComponentPropsWithoutRef<typeof RadixTabs.List>>(
  function TabsList({ className, ...rest }, ref) {
    const variant = useContext(VariantCtx);
    return (
      <RadixTabs.List
        ref={ref}
        className={cn(
          variant === 'underline'
            ? 'flex gap-1 border-b border-line'
            : 'inline-flex gap-0.5 rounded-[9px] bg-surface-3 p-[3px]',
          className,
        )}
        {...rest}
      />
    );
  },
);

export interface TabsTriggerProps extends ComponentPropsWithoutRef<typeof RadixTabs.Trigger> {
  icon?: ReactNode;
  /** Small "Soon" chip (prototype `.tabs .soon`); the text is supplied by the caller. */
  soon?: ReactNode;
}

export const TabsTrigger = forwardRef<HTMLButtonElement, TabsTriggerProps>(function TabsTrigger(
  { className, icon, soon, children, ...rest },
  ref,
) {
  const variant = useContext(VariantCtx);
  return (
    <RadixTabs.Trigger
      ref={ref}
      className={cn(
        'flex items-center gap-2 font-semibold text-fg-2 focus-ring transition-colors disabled:opacity-60',
        variant === 'underline'
          ? '-mb-px border-b-2 border-transparent px-3.5 py-[11px] hover:text-fg data-[state=active]:border-accent data-[state=active]:text-fg'
          : 'rounded-sm px-3 py-[5px] text-sm data-[state=active]:bg-surface-1 data-[state=active]:text-fg data-[state=active]:shadow-raised',
        className,
      )}
      {...rest}
    >
      {icon && (
        <span aria-hidden="true" className="flex">
          {icon}
        </span>
      )}
      {children}
      {soon && (
        <span className="rounded-[4px] border border-line-strong px-1.5 text-[10.5px] text-fg-3">
          {soon}
        </span>
      )}
    </RadixTabs.Trigger>
  );
});

export const TabsContent = forwardRef<
  HTMLDivElement,
  ComponentPropsWithoutRef<typeof RadixTabs.Content>
>(function TabsContent({ className, ...rest }, ref) {
  return (
    <RadixTabs.Content ref={ref} className={cn('pt-[18px] focus-ring', className)} {...rest} />
  );
});
