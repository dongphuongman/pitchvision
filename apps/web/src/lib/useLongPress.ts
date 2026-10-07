import { useEffect, useRef, type MouseEvent, type PointerEvent } from 'react';

const DELAY_MS = 500;
const MOVE_TOLERANCE_PX = 10;

/**
 * UIUX §09 "context menu → bottom sheet (nhấn giữ)": spread the returned handlers on the element that owns
 * a menu. Calls `onLongPress` after a 500 ms touch that doesn't move (a scroll cancels it), and on
 * `contextmenu` (right-click; Android's long-press) instead of the browser's own menu. The click that ends
 * a long press is swallowed so the element's link doesn't navigate. iOS fires no `contextmenu`: pair with
 * `[-webkit-touch-callout:none]` so Safari's link preview doesn't win.
 */
export function useLongPress(onLongPress: () => void) {
  const timer = useRef<number | undefined>(undefined);
  const start = useRef<{ x: number; y: number } | null>(null);
  const fired = useRef(false);
  const callback = useRef(onLongPress);
  useEffect(() => {
    callback.current = onLongPress;
  });

  const cancel = () => {
    window.clearTimeout(timer.current);
    timer.current = undefined;
    start.current = null;
  };
  const fire = () => {
    cancel();
    if (fired.current) return;
    fired.current = true;
    callback.current();
  };
  useEffect(() => () => window.clearTimeout(timer.current), []);

  return {
    onPointerDown: (e: PointerEvent) => {
      fired.current = false;
      if (e.pointerType !== 'touch') return;
      start.current = { x: e.clientX, y: e.clientY };
      timer.current = window.setTimeout(fire, DELAY_MS);
    },
    onPointerMove: (e: PointerEvent) => {
      const s = start.current;
      if (s && Math.hypot(e.clientX - s.x, e.clientY - s.y) > MOVE_TOLERANCE_PX) cancel();
    },
    onPointerUp: cancel,
    onPointerCancel: cancel,
    onContextMenu: (e: MouseEvent) => {
      e.preventDefault();
      fire();
    },
    onClickCapture: (e: MouseEvent) => {
      if (!fired.current) return;
      fired.current = false;
      e.preventDefault();
      e.stopPropagation();
    },
  };
}
