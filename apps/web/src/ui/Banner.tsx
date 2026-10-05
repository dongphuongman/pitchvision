import { CircleAlert, Info, TriangleAlert } from 'lucide-react';
import type { ReactNode } from 'react';
import { cn } from '@/lib/cn';

const TONES = {
  info: {
    box: 'border-[color-mix(in_srgb,var(--info)_30%,transparent)] bg-[color-mix(in_srgb,var(--info)_9%,transparent)]',
    icon: <Info size={18} className="text-info" aria-hidden="true" />,
  },
  warn: {
    box: 'border-[color-mix(in_srgb,var(--warn)_30%,transparent)] bg-[color-mix(in_srgb,var(--warn)_9%,transparent)]',
    icon: <TriangleAlert size={18} className="text-warn" aria-hidden="true" />,
  },
  err: {
    box: 'border-[color-mix(in_srgb,var(--dan)_30%,transparent)] bg-[color-mix(in_srgb,var(--dan)_9%,transparent)]',
    icon: <CircleAlert size={18} className="text-danger" aria-hidden="true" />,
  },
} as const;

export interface BannerProps {
  tone?: keyof typeof TONES;
  children: ReactNode;
  actions?: ReactNode;
  className?: string;
}

/** Prototype `.banner` — page-level warning / info / error with optional actions. */
export function Banner({ tone = 'info', children, actions, className }: BannerProps) {
  return (
    <div
      role={tone === 'err' ? 'alert' : 'status'}
      className={cn(
        'flex items-center gap-3 rounded-lg border px-3.5 py-2.5 text-control',
        TONES[tone].box,
        className,
      )}
    >
      {TONES[tone].icon}
      <div className="min-w-0 flex-1">{children}</div>
      {actions}
    </div>
  );
}
