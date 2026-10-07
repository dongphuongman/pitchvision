import { initMonitoring, runtimeConfig } from './monitoring';

const init = vi.fn();
vi.mock('@sentry/react', () => ({
  init: (o: unknown) => {
    init(o);
  },
}));

describe('monitoring (Sentry)', () => {
  afterEach(() => {
    init.mockReset();
    delete window.__PV_CONFIG__;
  });

  it('stays off without a DSN', async () => {
    expect(runtimeConfig()).toEqual({});
    expect(await initMonitoring()).toBe(false);
    expect(init).not.toHaveBeenCalled();
  });

  it('starts with the runtime config and no PII', async () => {
    window.__PV_CONFIG__ = {
      sentryDsn: 'https://k@o0.ingest.sentry.io/1',
      environment: 'staging',
      release: 'abc',
    };
    expect(await initMonitoring()).toBe(true);
    expect(init).toHaveBeenCalledWith(
      expect.objectContaining({ environment: 'staging', release: 'abc', sendDefaultPii: false }),
    );
  });
});
