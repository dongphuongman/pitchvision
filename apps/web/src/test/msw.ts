import { setupServer } from 'msw/node';
import { createMockDb, identityHandlers, type MockDb } from '@/mocks/identity';

/**
 * MSW for a test file: `const mock = setupIdentityMock()` at the top level of a describe/file.
 * Each test gets a fresh in-memory identity DB (`mock.db`), mutable before rendering.
 * Unhandled requests fail the test so contract drift is visible.
 */
export function setupIdentityMock() {
  const server = setupServer();
  const state: { db: MockDb } = { db: createMockDb() };

  beforeAll(() => server.listen({ onUnhandledFrame: 'error' }));
  beforeEach(() => {
    state.db = createMockDb();
    server.resetHandlers(...identityHandlers(state.db));
  });
  afterAll(() => server.close());

  return {
    server,
    get db() {
      return state.db;
    },
  };
}
