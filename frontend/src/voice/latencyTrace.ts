/** Structured client-side latency records. Never include PCM, credentials, or transcript text. */
export type LatencyTraceInput = {
  sessionId?: string | null;
  turnId?: string | null;
  responseId?: string | null;
  component: string;
  event: string;
  process?: string;
  monotonicNs?: number | null;
  monotonicMs?: number | null;
  clockDomain?: string;
  durationMs?: number | null;
  metadata?: Record<string, unknown>;
};

function monotonicTimestamp(): {
  monotonicMs: number | null;
  monotonicNs: number | null;
} {
  const performanceObject = (
    globalThis as { performance?: { now?: () => number } }
  ).performance;
  if (typeof performanceObject?.now === 'function') {
    const value = performanceObject.now();
    // Some React Native/Jest performance polyfills return epoch milliseconds.
    // Such values are wall time, not a monotonic clock, so leave those fields
    // empty instead of labeling them as monotonic.
    if (Number.isFinite(value) && value >= Date.now() / 2) {
      return { monotonicMs: null, monotonicNs: null };
    }
    if (Number.isFinite(value)) {
      return { monotonicMs: value, monotonicNs: Math.round(value * 1_000_000) };
    }
  }
  return { monotonicMs: null, monotonicNs: null };
}

function safeMetadata(
  metadata: Record<string, unknown> | undefined,
): Record<string, unknown> | undefined {
  if (!metadata) return undefined;
  const blocked =
    /(token|secret|password|authorization|api[_-]?key|credential)/i;
  return Object.fromEntries(
    Object.entries(metadata)
      .filter(([key]) => !blocked.test(key))
      .slice(0, 32),
  );
}

export function emitLatencyTrace(input: LatencyTraceInput): void {
  const metadata = safeMetadata(input.metadata);
  const monotonic = monotonicTimestamp();
  const inputNs = input.monotonicNs;
  const monotonicNs =
    typeof inputNs === 'number' && Number.isFinite(inputNs)
      ? inputNs
      : monotonic.monotonicNs;
  const inputMs = input.monotonicMs;
  const monotonicMs =
    typeof inputMs === 'number' && Number.isFinite(inputMs)
      ? inputMs
      : monotonicNs != null
      ? monotonicNs / 1_000_000
      : monotonic.monotonicMs;
  const wallTimeUtc = new Date().toISOString();
  const record = {
    timestamp: wallTimeUtc,
    wall_time_utc: wallTimeUtc,
    timestamp_ms: Date.now(),
    monotonic_ns: monotonicNs,
    monotonic_ms: monotonicMs == null ? null : Number(monotonicMs.toFixed(3)),
    process:
      input.process ??
      (input.clockDomain?.startsWith('android')
        ? 'android:com.voiceaipoc'
        : 'react_native'),
    clock_domain: input.clockDomain ?? 'react_native_performance',
    session_id: input.sessionId ?? null,
    turn_id: input.turnId ?? null,
    response_id: input.responseId ?? null,
    component: input.component,
    event: input.event,
    duration_ms: input.durationMs == null ? null : input.durationMs,
    metadata: metadata ?? {},
  };
  // Logcat/Metro collectors use this stable prefix. Logging is best-effort and
  // deliberately cannot affect the voice state machine.
  console.info(`LATENCY_TRACE ${JSON.stringify(record)}`);
}
