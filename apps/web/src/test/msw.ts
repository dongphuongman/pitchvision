import { setupServer } from 'msw/node';
import { resetWsClientForTests } from '@/api/ws';
import { resetPendingDeletesForTests } from '@/features/library/pendingDeletes';
import { resetUploadsForTests } from '@/features/upload/useUploads';
import { createMockDb, identityHandlers, type MockDb } from '@/mocks/identity';
import { resultsHandlers } from '@/mocks/results';
import { createVideoDb, videoHandlers, type MockVideoDb } from '@/mocks/videos';

/**
 * MSW for a test file: `const mock = setupIdentityMock()` at the top level of a describe/file.
 * Each test gets a fresh in-memory identity DB (`mock.db`) and videos/jobs DB (`mock.videos`,
 * incl. the `/api/v1/ws` socket), mutable before rendering. Unhandled requests fail the test so
 * contract drift is visible.
 */
export function setupIdentityMock() {
  const server = setupServer();
  const state: { db: MockDb; videos: MockVideoDb } = {
    db: createMockDb(),
    videos: createVideoDb(),
  };

  beforeAll(() => server.listen({ onUnhandledFrame: 'error' }));
  beforeEach(() => {
    state.db = createMockDb();
    state.videos = createVideoDb();
    server.resetHandlers(
      ...identityHandlers(state.db),
      ...videoHandlers(state.videos),
      ...resultsHandlers(state.videos),
    );
  });
  afterEach(() => {
    resetWsClientForTests();
    resetPendingDeletesForTests();
    resetUploadsForTests();
  });
  afterAll(() => server.close());

  return {
    server,
    get db() {
      return state.db;
    },
    get videos() {
      return state.videos;
    },
  };
}
