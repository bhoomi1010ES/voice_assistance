import { emitLatencyTrace } from '../src/voice/latencyTrace';

describe('latency trace clocks', () => {
  afterEach(() => {
    jest.restoreAllMocks();
  });

  it('does not label an epoch-based performance polyfill as monotonic', () => {
    const wallNow = Date.now();
    const performanceObject = (
      globalThis as { performance?: { now: () => number } }
    ).performance;
    if (!performanceObject) {
      throw new Error('The test runtime must expose performance.now().');
    }
    jest.spyOn(performanceObject, 'now').mockReturnValue(wallNow);
    const log = jest.spyOn(console, 'info').mockImplementation(() => {});

    emitLatencyTrace({ component: 'client', event: 'test_event' });

    const record = JSON.parse(
      String(log.mock.calls[0]?.[0]).replace('LATENCY_TRACE ', ''),
    );
    expect(record.monotonic_ns).toBeNull();
    expect(record.monotonic_ms).toBeNull();
    expect(record.timestamp_ms).toBeGreaterThanOrEqual(wallNow);
    expect(record.timestamp_ms).toBeLessThanOrEqual(wallNow + 100);
  });

  it('preserves a native monotonic timestamp and its clock domain', () => {
    const log = jest.spyOn(console, 'info').mockImplementation(() => {});

    emitLatencyTrace({
      component: 'android',
      event: 'speech_end',
      monotonicNs: 3_456_789,
      monotonicMs: 3.456789,
      clockDomain: 'android',
    });

    const record = JSON.parse(
      String(log.mock.calls[0]?.[0]).replace('LATENCY_TRACE ', ''),
    );
    expect(record.monotonic_ns).toBe(3_456_789);
    expect(record.monotonic_ms).toBe(3.457);
    expect(record.clock_domain).toBe('android');
  });
});
