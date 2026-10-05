import { Eye, EyeOff } from 'lucide-react';
import { forwardRef, useState } from 'react';
import { cn } from '@/lib/cn';
import { Input, type InputProps } from './Input';
import { useUiStrings } from './strings';

/** Password field with the show / hide toggle (UIUX SCR-02 E03 "👁"). */
export const PasswordInput = forwardRef<HTMLInputElement, Omit<InputProps, 'type' | 'trailing'>>(
  function PasswordInput(props, ref) {
    const strings = useUiStrings();
    const [visible, setVisible] = useState(false);
    return (
      <Input
        ref={ref}
        {...props}
        type={visible ? 'text' : 'password'}
        trailing={
          <button
            type="button"
            className="-mr-1 grid size-7 place-items-center rounded-sm text-fg-3 focus-ring hover:text-fg"
            aria-label={visible ? strings.hidePassword : strings.showPassword}
            aria-pressed={visible}
            onClick={() => setVisible((v) => !v)}
          >
            {visible ? (
              <EyeOff size={16} aria-hidden="true" />
            ) : (
              <Eye size={16} aria-hidden="true" />
            )}
          </button>
        }
      />
    );
  },
);

/**
 * Prototype `.strength`: 4 bars. Decorative — pair it with a text level in the field help so
 * the strength is announced (Field wires the help to aria-describedby).
 */
export function StrengthMeter({
  score,
  className,
}: {
  score: 0 | 1 | 2 | 3 | 4;
  className?: string;
}) {
  const tone = score <= 1 ? 'bg-danger' : score === 2 ? 'bg-warn' : 'bg-accent';
  return (
    <div className={cn('flex gap-1', className)} aria-hidden="true" data-score={score}>
      {[1, 2, 3, 4].map((i) => (
        <i key={i} className={cn('h-1 flex-1 rounded-[2px] bg-line-strong', i <= score && tone)} />
      ))}
    </div>
  );
}
