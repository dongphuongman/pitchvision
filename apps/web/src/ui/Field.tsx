import { Label } from 'radix-ui';
import { cloneElement, isValidElement, useId, type ReactElement, type ReactNode } from 'react';
import { cn } from '@/lib/cn';

export interface FieldProps {
  label: ReactNode;
  /** Helper text under the control. Replaced by `error` when present. */
  help?: ReactNode;
  error?: ReactNode;
  className?: string;
  /** A single form control; receives id, aria-describedby and aria-invalid. */
  children: ReactElement<{
    id?: string;
    'aria-describedby'?: string;
    'aria-invalid'?: boolean;
  }>;
}

/** Prototype `.field`: label + control + `.help` / `.help.err`, with ids wired for a11y. */
export function Field({ label, help, error, className, children }: FieldProps) {
  const id = useId();
  const controlId = children.props.id ?? `${id}-control`;
  const msgId = `${id}-msg`;
  const message = error ?? help;
  const control = isValidElement(children)
    ? cloneElement(children, {
        id: controlId,
        'aria-describedby': message ? msgId : undefined,
        'aria-invalid': error ? true : undefined,
      })
    : children;

  return (
    <div className={cn('grid gap-1.5', className)}>
      <Label.Root htmlFor={controlId} className="text-caption font-semibold text-fg-2">
        {label}
      </Label.Root>
      {control}
      {message && (
        <p
          id={msgId}
          role={error ? 'alert' : undefined}
          className={cn('m-0 text-caption', error ? 'text-danger' : 'text-fg-3')}
        >
          {message}
        </p>
      )}
    </div>
  );
}
