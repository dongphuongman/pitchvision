import '@fontsource-variable/inter';
import '@fontsource-variable/jetbrains-mono';
import './styles/index.css';
import './i18n';

import { StrictMode } from 'react';
import { createRoot } from 'react-dom/client';
import { App } from './App';
import { initMonitoring } from './lib/monitoring';

const rootEl = document.getElementById('root');
if (!rootEl) throw new Error('#root element missing from index.html');
const root = rootEl;

async function enableMocks(): Promise<void> {
  // Dead-code-eliminated in production builds (import.meta.env.DEV is statically false).
  if (import.meta.env.DEV && import.meta.env.VITE_MOCK_API === '1') {
    const { startMockApi } = await import('./mocks/browser');
    await startMockApi();
  }
}

void initMonitoring().catch(() => undefined); // never blocks the app

void enableMocks().then(() => {
  createRoot(root).render(
    <StrictMode>
      <App />
    </StrictMode>,
  );
});
