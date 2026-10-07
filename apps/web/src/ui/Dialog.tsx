import { X } from 'lucide-react';
import { Dialog as RadixDialog } from 'radix-ui';
import type { ReactNode } from 'react';
import { cn } from '@/lib/cn';
import { IconButton } from './IconButton';
import { useUiStrings } from './strings';

/** UIUX modal sizes; widths follow the prototype (`.modal.sm` 480 / `.modal` 660 / `.modal.lg` 920). */
const SIZES = { sm: 'md:w-[480px]', md: 'md:w-[660px]', lg: 'md:w-[920px]' } as const;

export interface DialogProps {
  open?: boolean;
  defaultOpen?: boolean;
  onOpenChange?: (open: boolean) => void;
  /** Optional trigger element; omit for dialogs driven by state / query string. */
  trigger?: ReactNode;
  title: ReactNode;
  /** Visible subtitle under the title; also wired as aria-describedby. */
  description?: ReactNode;
  size?: keyof typeof SIZES;
  /** Footer row (prototype `.mft`): put the primary action last (right). */
  footer?: ReactNode;
  children?: ReactNode;
  className?: string;
}

/**
 * Prototype `.mbg` + `.modal` (`.mhd` / `.mbd` / `.mft`). Focus is trapped, Esc and the overlay
 * close it, focus returns to the trigger. Below 768px it becomes a full-screen sheet (UIUX §09: < 768 "modal thành full-screen sheet").
 */
export function Dialog({
  open,
  defaultOpen,
  onOpenChange,
  trigger,
  title,
  description,
  size = 'md',
  footer,
  children,
  className,
}: DialogProps) {
  const strings = useUiStrings();
  return (
    <RadixDialog.Root open={open} defaultOpen={defaultOpen} onOpenChange={onOpenChange}>
      {trigger && <RadixDialog.Trigger asChild>{trigger}</RadixDialog.Trigger>}
      <RadixDialog.Portal>
        <RadixDialog.Overlay className="fixed inset-0 z-[80] bg-overlay backdrop-blur-[3px]" />
        <RadixDialog.Content
          {...(description ? {} : { 'aria-describedby': undefined })}
          className={cn(
            'fixed z-[81] flex flex-col overflow-hidden border border-line bg-surface-1 text-fg shadow-pop outline-none',
            'inset-0 md:inset-auto md:top-1/2 md:left-1/2 md:max-h-[calc(100vh-48px)] md:max-w-[calc(100vw-48px)] md:-translate-x-1/2 md:-translate-y-1/2 md:rounded-3xl',
            SIZES[size],
            className,
          )}
        >
          <div className="flex items-center justify-between gap-3 border-b border-line px-[22px] py-[18px]">
            <div className="min-w-0">
              <RadixDialog.Title className="m-0 text-lg font-bold">{title}</RadixDialog.Title>
              {description && (
                <RadixDialog.Description className="m-0 mt-0.5 text-control text-fg-2">
                  {description}
                </RadixDialog.Description>
              )}
            </div>
            <RadixDialog.Close asChild>
              <IconButton label={strings.close} icon={<X size={18} />} tooltip={false} />
            </RadixDialog.Close>
          </div>
          <div className="min-h-0 flex-1 overflow-auto px-[22px] py-5">{children}</div>
          {footer && (
            <div className="flex items-center justify-end gap-2.5 border-t border-line px-[22px] py-3.5">
              {footer}
            </div>
          )}
        </RadixDialog.Content>
      </RadixDialog.Portal>
    </RadixDialog.Root>
  );
}

/** Wrap a footer button so clicking it closes the dialog (e.g. Cancel). */
export const DialogClose = RadixDialog.Close;
