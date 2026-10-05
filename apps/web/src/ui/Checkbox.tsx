import { Check, Minus } from 'lucide-react';
import { Checkbox as RadixCheckbox } from 'radix-ui';
import { forwardRef, useId, type ComponentPropsWithoutRef, type ReactNode } from 'react';
import { cn } from '@/lib/cn';

export interface CheckboxProps extends ComponentPropsWithoutRef<typeof RadixCheckbox.Root> {
  label?: ReactNode;
  description?: ReactNode;
}

/** Prototype `.chk` + `.box` (18px, radius 5, accent when on). */
export const Checkbox = forwardRef<HTMLButtonElement, CheckboxProps>(function Checkbox(
  { label, description, className, id, ...rest },
  ref,
) {
  const autoId = useId();
  const boxId = id ?? autoId;
  const box = (
    <RadixCheckbox.Root
      ref={ref}
      id={boxId}
      className={cn(
        'mt-px grid size-[18px] flex-none place-items-center rounded-xs border-[1.5px] border-line-strong bg-transparent focus-ring',
        'data-[state=checked]:border-accent data-[state=checked]:bg-accent data-[state=checked]:text-accent-ink',
        'data-[state=indeterminate]:border-accent data-[state=indeterminate]:bg-accent data-[state=indeterminate]:text-accent-ink',
        'disabled:cursor-not-allowed disabled:opacity-45',
        !label && className,
      )}
      {...rest}
    >
      <RadixCheckbox.Indicator>
        {rest.checked === 'indeterminate' ? (
          <Minus size={12} strokeWidth={3} aria-hidden="true" />
        ) : (
          <Check size={12} strokeWidth={3} aria-hidden="true" />
        )}
      </RadixCheckbox.Indicator>
    </RadixCheckbox.Root>
  );
  if (!label) return box;
  return (
    <div className={cn('flex items-start gap-2.5 text-control text-fg-2', className)}>
      {box}
      <label htmlFor={boxId} className="cursor-pointer">
        <span className="text-fg">{label}</span>
        {description && <span className="block text-caption text-fg-3">{description}</span>}
      </label>
    </div>
  );
});
