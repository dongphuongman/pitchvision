import { act, renderHook } from '@testing-library/react';
import type { MouseEvent, PointerEvent } from 'react';
import { useLongPress } from './useLongPress';

const touch = (x = 10, y = 10) =>
  ({ pointerType: 'touch', clientX: x, clientY: y }) as PointerEvent;
const mouse = () => ({ pointerType: 'mouse', clientX: 0, clientY: 0 }) as PointerEvent;
const click = () => {
  const prevent = vi.fn();
  const e = { preventDefault: prevent, stopPropagation: vi.fn() } as unknown as MouseEvent;
  return { e, prevent };
};

describe('useLongPress', () => {
  beforeEach(() => vi.useFakeTimers());
  afterEach(() => vi.useRealTimers());

  it('fires after a 500 ms touch and swallows the click that ends it', () => {
    const cb = vi.fn();
    const { result } = renderHook(() => useLongPress(cb));
    act(() => {
      result.current.onPointerDown(touch());
    });
    act(() => {
      vi.advanceTimersByTime(499);
    });
    expect(cb).not.toHaveBeenCalled();
    act(() => {
      vi.advanceTimersByTime(1);
    });
    expect(cb).toHaveBeenCalledTimes(1);
    const c = click();
    result.current.onClickCapture(c.e);
    expect(c.prevent).toHaveBeenCalled();
    const next = click(); // only that one click
    result.current.onClickCapture(next.e);
    expect(next.prevent).not.toHaveBeenCalled();
  });

  it('a short tap, a scroll or a mouse press never fires', () => {
    const cb = vi.fn();
    const { result } = renderHook(() => useLongPress(cb));
    act(() => {
      result.current.onPointerDown(touch());
    });
    act(() => {
      vi.advanceTimersByTime(200);
    });
    act(() => {
      result.current.onPointerUp();
    });
    act(() => {
      result.current.onPointerDown(touch());
    });
    act(() => {
      result.current.onPointerMove(touch(10, 40));
    }); // scrolling the list
    act(() => {
      result.current.onPointerDown(mouse());
    });
    act(() => {
      vi.advanceTimersByTime(2000);
    });
    expect(cb).not.toHaveBeenCalled();
  });

  it('contextmenu (right-click / Android long-press) opens instead of the browser menu, once', () => {
    const cb = vi.fn();
    const { result } = renderHook(() => useLongPress(cb));
    act(() => {
      result.current.onPointerDown(touch());
    });
    act(() => {
      vi.advanceTimersByTime(500);
    });
    const { e, prevent } = click();
    act(() => {
      result.current.onContextMenu(e);
    }); // Android sends both: still one call
    expect(prevent).toHaveBeenCalled();
    expect(cb).toHaveBeenCalledTimes(1);
  });
});
