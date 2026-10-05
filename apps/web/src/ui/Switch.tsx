import { Switch as RadixSwitch } from 'radix-ui';
import { forwardRef, useId, type ComponentPropsWithoutRef, type ReactNode } from 'react';
import { cn } from '@/lib/cn';

export interface SwitchProps extends ComponentPropsWithoutRef<typeof RadixSwitch.Root> {
  label?: ReactNode;
}

/** Prototype `.tgl` (36×20, knob 16px). role="switch" + aria-checked via Radix. */
export const Switch = forwardRef<HTMLButtonElement, SwitchProps>(function Switch(
  { label, className, id, ...rest },
  ref,
) {
  const autoId = useId();
  const switchId = id ?? autoId;
  const control = (
    <RadixSwitch.Root
      ref={ref}
      id={switchId}
      className={cn(
        'relative h-5 w-9 flex-none rounded-[10px] bg-line-strong focus-ring transition-colors duration-150',
        'disabled:cursor-not-allowed disabled:opacity-45 data-[state=checked]:bg-accent',
        !label && className,
      )}
      {...rest}
    >
      <RadixSwitch.Thumb className="block size-4 translate-x-0.5 rounded-full bg-white shadow-raised transition-transform duration-150 data-[state=checked]:translate-x-[18px]" />
    </RadixSwitch.Root>
  );
  if (!label) return control;
  return (
    <div className={cn('flex items-center gap-2.5 text-control text-fg', className)}>
      {control}
      <label htmlFor={switchId} className="cursor-pointer">
        {label}
      </label>
    </div>
  );
});
