import { clsx, type ClassValue } from 'clsx';
import { extendTailwindMerge } from 'tailwind-merge';

// Teach tailwind-merge about the custom type scale so `text-sm` and `text-fg-2` don't collide.
const twMerge = extendTailwindMerge({
  extend: {
    classGroups: {
      'font-size': [
        { text: ['2xs', 'xs', 'caption', 'sm', 'control', 'base', 'md', 'lg', 'xl', '2xl', '3xl'] },
      ],
    },
  },
});

export function cn(...inputs: ClassValue[]): string {
  return twMerge(clsx(inputs));
}
