import { useEffect, useRef } from 'react';

/**
 * Moves focus to the returned element whenever `key` changes (wizard step, success / error
 * state…) so keyboard and screen-reader users land on the new content. Not on first mount
 * unless `onMount` is set (avoid stealing focus from autofocus fields).
 */
export function useFocusOnChange<T extends HTMLElement>(key: unknown, onMount = false) {
  const ref = useRef<T>(null);
  const first = useRef(true);
  useEffect(() => {
    if (first.current) {
      first.current = false;
      if (!onMount) return;
    }
    ref.current?.focus();
  }, [key, onMount]);
  return ref;
}
