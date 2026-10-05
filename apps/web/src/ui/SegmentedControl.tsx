import { RadioGroup } from 'radix-ui';
import { forwardRef, type ReactNode } from 'react';
import { cn } from '@/lib/cn';

export interface SegmentedOption<V extends string = string> {
  value: V;
  label: ReactNode;
  disabled?: boolean;
}

export interface SegmentedControlProps<V extends string = string> {
  options: readonly SegmentedOption<V>[];
  value?: V;
  onValueChange?: (value: V) => void;
  disabled?: boolean;
  id?: string;
  className?: string;
  'aria-label'?: string;
  'aria-labelledby'?: string;
  'aria-describedby'?: string;
  'aria-invalid'?: boolean;
}

/**
 * Prototype `.seg`: single-choice pill group. A radio group under the hood (arrow keys move the
 * selection, one tab stop), so pair it with a visible label via `aria-labelledby` / Field.
 */
export const SegmentedControl = forwardRef<HTMLDivElement, SegmentedControlProps>(
  function SegmentedControl(
    { options, value, onValueChange, disabled, className, 'aria-invalid': ariaInvalid, ...rest },
    ref,
  ) {
    return (
      <RadioGroup.Root
        ref={ref}
        value={value}
        onValueChange={onValueChange}
        disabled={disabled}
        orientation="horizontal"
        aria-invalid={ariaInvalid || undefined}
        className={cn(
          'inline-flex flex-wrap gap-0.5 rounded-[9px] bg-surface-3 p-[3px] aria-invalid:ring-1 aria-invalid:ring-danger',
          className,
        )}
        {...rest}
      >
        {options.map((opt) => (
          <RadioGroup.Item
            key={opt.value}
            value={opt.value}
            disabled={opt.disabled}
            className={cn(
              'rounded-[7px] px-3 py-[5px] text-sm font-semibold text-fg-2 focus-ring',
              'transition-colors duration-[120ms] hover:text-fg',
              'data-[state=checked]:bg-surface-1 data-[state=checked]:text-fg data-[state=checked]:shadow-raised',
              'disabled:cursor-not-allowed disabled:opacity-55',
            )}
          >
            {opt.label}
          </RadioGroup.Item>
        ))}
      </RadioGroup.Root>
    );
  },
) as <V extends string = string>(
  props: SegmentedControlProps<V> & { ref?: React.Ref<HTMLDivElement> },
) => React.ReactElement;
