import type { ReactNode } from 'react';
import { create } from 'zustand';

export type ToastTone = 'neutral' | 'success' | 'info' | 'error';

export interface ToastOptions {
  title: ReactNode;
  description?: ReactNode;
  tone?: ToastTone;
  /** e.g. Undo (UIUX: edits get a 10 s Undo toast) or "Open". */
  action?: { label: string; onClick: () => void; altText?: string };
  /** ms; default 3200 as in the prototype, 10 000 when an action is present. */
  duration?: number;
}

interface ToastItem extends ToastOptions {
  id: number;
  open: boolean;
}

interface ToastState {
  toasts: ToastItem[];
  push: (t: ToastOptions) => number;
  close: (id: number) => void;
  remove: (id: number) => void;
}

let nextId = 1;

export const useToastStore = create<ToastState>()((set) => ({
  toasts: [],
  push: (t) => {
    const id = nextId++;
    set((s) => ({ toasts: [...s.toasts.slice(-3), { ...t, id, open: true }] }));
    return id;
  },
  close: (id) =>
    set((s) => ({ toasts: s.toasts.map((t) => (t.id === id ? { ...t, open: false } : t)) })),
  remove: (id) => set((s) => ({ toasts: s.toasts.filter((t) => t.id !== id) })),
}));

/** Imperative API usable outside React (e.g. in mutation callbacks). */
export function toast(options: ToastOptions): number {
  return useToastStore.getState().push(options);
}
