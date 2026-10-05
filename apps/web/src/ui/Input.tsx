import {
  forwardRef,
  type InputHTMLAttributes,
  type ReactNode,
  type TextareaHTMLAttributes,
} from 'react';
import { cn } from '@/lib/cn';
import { controlClasses } from './classes';

export interface InputProps extends InputHTMLAttributes<HTMLInputElement> {
  /** Icon or adornment shown inside the field on the left (e.g. search icon). */
  leading?: ReactNode;
  trailing?: ReactNode;
  invalid?: boolean;
}

export const Input = forwardRef<HTMLInputElement, InputProps>(function Input(
  { leading, trailing, invalid, className, type, 'aria-invalid': ariaInvalid, ...rest },
  ref,
) {
  const input = (
    <input
      ref={ref}
      type={type ?? 'text'}
      aria-invalid={invalid || ariaInvalid || undefined}
      className={cn(
        controlClasses,
        'h-[38px] px-3',
        leading !== undefined && 'pl-9',
        trailing !== undefined && 'pr-9',
        !leading && !trailing && className,
      )}
      {...rest}
    />
  );
  if (leading === undefined && trailing === undefined) return input;
  return (
    <div className={cn('relative flex items-center', className)}>
      {leading !== undefined && (
        <span className="pointer-events-none absolute left-3 flex text-fg-3" aria-hidden="true">
          {leading}
        </span>
      )}
      {input}
      {trailing !== undefined && (
        <span className="absolute right-3 flex text-fg-3">{trailing}</span>
      )}
    </div>
  );
});

export interface TextareaProps extends TextareaHTMLAttributes<HTMLTextAreaElement> {
  invalid?: boolean;
}

export const Textarea = forwardRef<HTMLTextAreaElement, TextareaProps>(function Textarea(
  { invalid, className, rows, 'aria-invalid': ariaInvalid, ...rest },
  ref,
) {
  return (
    <textarea
      ref={ref}
      rows={rows ?? 4}
      aria-invalid={invalid || ariaInvalid || undefined}
      className={cn(controlClasses, 'min-h-[76px] resize-y px-3 py-2 leading-normal', className)}
      {...rest}
    />
  );
});
