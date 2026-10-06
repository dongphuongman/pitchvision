import { useEffect, useState } from 'react';

/**
 * True once `el` has been (nearly) visible — for lazy loading. Stays true afterwards. Without
 * IntersectionObserver (tests, old browsers) it stays false: callers load on demand instead.
 */
export function useInView(el: Element | null, rootMargin = '200px'): boolean {
  const [seen, setSeen] = useState(false);
  useEffect(() => {
    if (!el || seen || typeof IntersectionObserver === 'undefined') return;
    const io = new IntersectionObserver(
      (entries) => {
        if (entries.some((e) => e.isIntersecting)) setSeen(true);
      },
      { rootMargin },
    );
    io.observe(el);
    return () => io.disconnect();
  }, [el, seen, rootMargin]);
  return seen;
}
