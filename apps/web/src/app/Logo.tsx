import { cn } from '@/lib/cn';

/** Prototype `.logo`: green gradient tile with a centre-circle mark + wordmark. */
export function Logo({ size = 'md', label }: { size?: 'md' | 'lg'; label: string }) {
  return (
    <span
      className={cn(
        'flex items-center gap-[9px] font-extrabold tracking-[-.02em]',
        size === 'lg' ? 'text-xl' : 'text-[15.5px]',
      )}
    >
      <i
        aria-hidden="true"
        className={cn(
          'relative rounded-sm bg-[linear-gradient(135deg,#3ddc84,#1b8f52)]',
          'after:absolute after:inset-1.5 after:rounded-full after:border-2 after:border-[#04210f] after:content-[""]',
          size === 'lg' ? 'size-[30px]' : 'size-6',
        )}
      />
      {label}
    </span>
  );
}
