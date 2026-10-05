import { Tooltip as RadixTooltip } from 'radix-ui';
import type { ReactNode } from 'react';
import { cn } from '@/lib/cn';

export const TooltipProvider = RadixTooltip.Provider;

export interface TooltipProps {
  content: ReactNode;
  children: ReactNode;
  side?: 'top' | 'right' | 'bottom' | 'left';
  /** Keyboard shortcut rendered next to the text, e.g. <Kbd>N</Kbd>. */
  shortcut?: ReactNode;
  className?: string;
}

/** Needs a <TooltipProvider> ancestor (mounted once in the app root). Opens on hover and focus. */
export function Tooltip({ content, children, side = 'top', shortcut, className }: TooltipProps) {
  return (
    <RadixTooltip.Root>
      <RadixTooltip.Trigger asChild>{children}</RadixTooltip.Trigger>
      <RadixTooltip.Portal>
        <RadixTooltip.Content
          side={side}
          sideOffset={6}
          collisionPadding={8}
          className={cn(
            'z-[90] flex max-w-72 items-center gap-2 rounded-sm border border-toast-line bg-toast px-2 py-1 text-xs font-medium text-toast-fg shadow-pop',
            className,
          )}
        >
          {content}
          {shortcut}
        </RadixTooltip.Content>
      </RadixTooltip.Portal>
    </RadixTooltip.Root>
  );
}
