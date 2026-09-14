/** Structured client-side latency records. Never include PCM, credentials, or transcript text. */
export type LatencyTraceInput = {
  sessionId?: string | null;
  turnId?: string | null;
  responseId?: string | null;
  component: string;
  event: string;
  durationMs?: number | null;
  metadata?: Record<string, unknown>;
};

function monotonicMs(): number {
  const performanceObject = (globalThis as { performance?: { now?: () => number } }).performance;
  return typeof performanceObject?.now === 'function' ? performanceObject.now() : Date.now();
}

function safeMetadata(
  metadata: Record<string, unknown> | undefined,
): Record<string, unknown> | undefined {
  if (!metadata) return undefined;
  const blocked = /(token|secret|password|authorization|api[_-]?key|credential)/i;
  return Object.fromEntries(
    Object.entries(metadata).filter(([key]) => !blocked.test(key)).slice(0, 32),
  );
}

export function emitLatencyTrace(input: LatencyTraceInput): void {
  const metadata = safeMetadata(input.metadata);
  const record = {
    timestamp: new Date().toISOString(),
    timestamp_ms: Date.now(),
    monotonic_ms: Number(monotonicMs().toFixed(3)),
    clock_domain: 'client',
    session_id: input.sessionId ?? null,
    turn_id: input.turnId ?? null,
    response_id: input.responseId ?? null,
    component: input.component,
    event: input.event,
    ...(input.durationMs == null ? {} : { duration_ms: Math.max(0, input.durationMs) }),
    ...(metadata ? { metadata } : {}),
  };
  // Logcat/Metro collectors use this stable prefix. Logging is best-effort and
  // deliberately cannot affect the voice state machine.
  console.info(`LATENCY_TRACE ${JSON.stringify(record)}`);
}
