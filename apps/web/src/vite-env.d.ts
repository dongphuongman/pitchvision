/// <reference types="vite/client" />

interface ImportMetaEnv {
  /** Base URL of the PitchVision API, e.g. https://eu.api.pitchvision.ai. Same-origin when empty. */
  readonly VITE_API_URL?: string;
  /** Base URL of the global identity service. Same-origin when empty. */
  readonly VITE_IDENTITY_URL?: string;
  /** Dev only: "1" serves the API from in-browser MSW mocks (src/mocks). */
  readonly VITE_MOCK_API?: string;
}

/** apps/web package.json version, injected at build time. */
declare const __APP_VERSION__: string;

interface ImportMeta {
  readonly env: ImportMetaEnv;
}
