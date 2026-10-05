import '@testing-library/jest-dom/vitest';
import { cleanup, configure } from '@testing-library/react';
import { afterEach } from 'vitest';
import '@/i18n';
import { resetAuthForTests } from '@/auth/session';
import { useOnboardingDraft } from '@/features/onboarding/draft';
import { useToastStore } from '@/ui';

// Full-app tests wait for the session bootstrap + lazily imported route chunks; the 1 s default
// is too tight when the whole suite runs in parallel.
configure({ asyncUtilTimeout: 4000 });

afterEach(() => {
  cleanup();
  document.documentElement.removeAttribute('data-theme');
  localStorage.clear();
  sessionStorage.clear();
  resetAuthForTests();
  useOnboardingDraft.getState().reset();
  useToastStore.setState({ toasts: [] });
});

// jsdom lacks these; Radix primitives and theme code rely on them.
if (typeof window.matchMedia !== 'function') {
  Object.defineProperty(window, 'matchMedia', {
    writable: true,
    value: (query: string): MediaQueryList => ({
      matches: false,
      media: query,
      onchange: null,
      addListener: () => undefined,
      removeListener: () => undefined,
      addEventListener: () => undefined,
      removeEventListener: () => undefined,
      dispatchEvent: () => false,
    }),
  });
}

class ResizeObserverStub {
  observe(): void {}
  unobserve(): void {}
  disconnect(): void {}
}
if (!('ResizeObserver' in globalThis)) {
  Object.assign(globalThis, { ResizeObserver: ResizeObserverStub });
}

if (!('hasPointerCapture' in Element.prototype)) {
  Object.assign(Element.prototype, {
    hasPointerCapture: () => false,
    setPointerCapture: () => undefined,
    releasePointerCapture: () => undefined,
  });
}
if (!('scrollIntoView' in Element.prototype)) {
  Object.assign(Element.prototype, { scrollIntoView: () => undefined });
}
