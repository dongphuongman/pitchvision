import { DropdownMenu as RadixMenu } from 'radix-ui';
import { forwardRef, type ComponentPropsWithoutRef, type ReactNode } from 'react';
import { cn } from '@/lib/cn';
import { menuContentClasses, menuItemClasses } from './classes';

export const DropdownMenu = RadixMenu.Root;
export const DropdownMenuTrigger = RadixMenu.Trigger;
export const DropdownMenuGroup = RadixMenu.Group;
export const DropdownMenuSub = RadixMenu.Sub;

export const DropdownMenuContent = forwardRef<
  HTMLDivElement,
  ComponentPropsWithoutRef<typeof RadixMenu.Content>
>(function DropdownMenuContent({ className, sideOffset = 6, align = 'end', ...rest }, ref) {
  return (
    <RadixMenu.Portal>
      <RadixMenu.Content
        ref={ref}
        sideOffset={sideOffset}
        align={align}
        collisionPadding={8}
        className={cn(menuContentClasses, className)}
        {...rest}
      />
    </RadixMenu.Portal>
  );
});

export interface DropdownMenuItemProps extends ComponentPropsWithoutRef<typeof RadixMenu.Item> {
  icon?: ReactNode;
  /** Right-aligned shortcut hint, e.g. <Kbd>?</Kbd>. */
  shortcut?: ReactNode;
  /** Prototype `.menu a.dn` — destructive item. */
  danger?: boolean;
}

export const DropdownMenuItem = forwardRef<HTMLDivElement, DropdownMenuItemProps>(
  function DropdownMenuItem({ className, icon, shortcut, danger, children, ...rest }, ref) {
    return (
      <RadixMenu.Item
        ref={ref}
        className={cn(menuItemClasses, danger && 'text-danger', className)}
        {...rest}
      >
        {icon && (
          <span aria-hidden="true" className="flex">
            {icon}
          </span>
        )}
        <span className="flex-1">{children}</span>
        {shortcut && <span className="ml-auto pl-3">{shortcut}</span>}
      </RadixMenu.Item>
    );
  },
);

export function DropdownMenuLabel({
  className,
  ...rest
}: ComponentPropsWithoutRef<typeof RadixMenu.Label>) {
  return (
    <RadixMenu.Label className={cn('px-2.5 pt-2 pb-1.5 text-xs text-fg-3', className)} {...rest} />
  );
}

export function DropdownMenuSeparator({
  className,
  ...rest
}: ComponentPropsWithoutRef<typeof RadixMenu.Separator>) {
  return <RadixMenu.Separator className={cn('mx-0.5 my-1.5 h-px bg-line', className)} {...rest} />;
}
