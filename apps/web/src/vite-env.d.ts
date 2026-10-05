/// <reference types="vite/client" />

interface ImportMetaEnv {
  /** Base URL of the PitchVision API, e.g. https://eu.api.pitchvision.ai. Same-origin when empty. */
  readonly VITE_API_URL?: string;
}

/** apps/web package.json version, injected at build time. */
declare const __APP_VERSION__: string;

interface ImportMeta {
  readonly env: ImportMetaEnv;
}
