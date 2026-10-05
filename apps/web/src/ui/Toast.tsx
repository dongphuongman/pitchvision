import { CircleAlert, CircleCheck, Info, X } from 'lucide-react';
import { Toast as RadixToast } from 'radix-ui';
import type { ReactNode } from 'react';
import { cn } from '@/lib/cn';
import { useUiStrings } from './strings';
import { useToastStore, type ToastTone } from './toast-store';

const TONE_ICON: Record<ToastTone, ReactNode> = {
  neutral: null,
  success: <CircleCheck size={16} className="text-[#3ddc84]" aria-hidden="true" />,
  info: <Info size={16} className="text-[#4da3ff]" aria-hidden="true" />,
  error: <CircleAlert size={16} className="text-[#ff6b6b]" aria-hidden="true" />,
};

/** Mount once at the app root. Prototype `#toast .t`: dark pill, bottom-centre, 84px up. */
export function Toaster() {
  const toasts = useToastStore((s) => s.toasts);
  const close = useToastStore((s) => s.close);
  const remove = useToastStore((s) => s.remove);
  const strings = useUiStrings();

  return (
    <RadixToast.Provider swipeDirection="down" label={strings.notifications}>
      {toasts.map((t) => (
        <RadixToast.Root
          key={t.id}
          open={t.open}
          duration={t.duration ?? (t.action ? 10_000 : 3200)}
          type={t.tone === 'error' ? 'foreground' : 'background'}
          onOpenChange={(open) => {
            if (!open) {
              close(t.id);
              window.setTimeout(() => remove(t.id), 200);
            }
          }}
          className={cn(
            'flex items-center gap-3.5 rounded-xl border border-toast-line bg-toast px-3.5 py-[11px] text-control text-toast-fg shadow-pop',
            'transition-[opacity,transform] duration-200 data-[state=closed]:translate-y-5 data-[state=closed]:opacity-0',
            'data-[swipe=move]:translate-y-[var(--radix-toast-swipe-move-y)]',
          )}
        >
          {TONE_ICON[t.tone ?? 'neutral']}
          <div className="min-w-0 flex-1">
            <RadixToast.Title className="font-medium">{t.title}</RadixToast.Title>
            {t.description && (
              <RadixToast.Description className="text-caption text-[#a3b3c4]">
                {t.description}
              </RadixToast.Description>
            )}
          </div>
          {t.action && (
            <RadixToast.Action
              altText={t.action.altText ?? t.action.label}
              onClick={t.action.onClick}
              className="rounded-xs font-semibold text-[#3ddc84] focus-ring hover:underline"
            >
              {t.action.label}
            </RadixToast.Action>
          )}
          <RadixToast.Close
            aria-label={strings.dismiss}
            className="grid size-6 place-items-center rounded-xs text-[#a3b3c4] focus-ring hover:text-white"
          >
            <X size={14} aria-hidden="true" />
          </RadixToast.Close>
        </RadixToast.Root>
      ))}
      <RadixToast.Viewport className="fixed bottom-[84px] left-1/2 z-[200] m-0 flex w-max max-w-[calc(100vw-32px)] -translate-x-1/2 list-none flex-col gap-2 p-0 outline-none" />
    </RadixToast.Provider>
  );
}
