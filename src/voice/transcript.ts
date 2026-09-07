export type TranscriptEventKind = 'partial' | 'final';

export type VoiceTranscriptMetrics = Record<string, number | null>;

/** Provider-neutral transcript event consumed by the UI state machine. */
export type CanonicalTranscriptEvent = {
  kind: TranscriptEventKind;
  sessionId: string | null;
  turnId: string | null;
  responseId: string | null;
  text: string;
  sequence: number | null;
  timestampMs: number | null;
  language: string | null;
  audioDurationMs: number | null;
  metrics: VoiceTranscriptMetrics;
};

export type TranscriptMessageStatus =
  | 'listening'
  | 'speech_detected'
  | 'transcribing'
  | 'partial'
  | 'final'
  | 'error'
  | 'cancelled';

export type VoiceTranscriptMessage = {
  id: string;
  turnId: string;
  responseId: string | null;
  text: string;
  status: TranscriptMessageStatus;
  final: boolean;
  transcriptSequence: number | null;
  audioDurationMs: number | null;
  language: string | null;
  startedAtMs: number;
  speechEndedAtMs: number | null;
  finalReceivedAtMs: number | null;
  uiSpeechEndToFinalMs: number | null;
  serverSpeechEndToFinalMs: number | null;
  errorCode: string | null;
};

export type MappedTranscriptError = {
  code: string;
  message: string;
  retryable: boolean;
};

const MAX_MESSAGES = 50;

const TRANSCRIPT_ERRORS: Record<string, MappedTranscriptError> = {
  stt_unavailable: {
    code: 'stt_unavailable',
    message: 'Speech recognition is unavailable. Try again shortly.',
    retryable: true,
  },
  stt_timeout: {
    code: 'stt_timeout',
    message: 'Speech recognition took too long. Try again.',
    retryable: true,
  },
  stt_configuration_error: {
    code: 'stt_configuration_error',
    message: 'Speech recognition needs configuration. Contact support.',
    retryable: false,
  },
  stt_authentication_error: {
    code: 'stt_authentication_error',
    message: 'Speech recognition is not authorized. Contact support.',
    retryable: false,
  },
  stt_rate_limited: {
    code: 'stt_rate_limited',
    message: 'Too many transcription requests. Wait a moment and try again.',
    retryable: true,
  },
  stt_network_error: {
    code: 'stt_network_error',
    message:
      'Could not reach speech recognition. Check your connection and try again.',
    retryable: true,
  },
  stt_invalid_audio: {
    code: 'stt_invalid_audio',
    message: 'No usable microphone audio was captured. Try again.',
    retryable: true,
  },
  stt_empty_audio: {
    code: 'stt_empty_audio',
    message: "I didn't hear anything. Try speaking again.",
    retryable: true,
  },
  stt_empty_transcript: {
    code: 'stt_empty_transcript',
    message: "I couldn't make out any words. Try again.",
    retryable: true,
  },
  stt_audio_too_long: {
    code: 'stt_audio_too_long',
    message: 'That recording is too long. Try a shorter one.',
    retryable: true,
  },
  stt_cancelled: {
    code: 'stt_cancelled',
    message: 'Transcription was cancelled.',
    retryable: true,
  },
  stt_inference_error: {
    code: 'stt_inference_error',
    message: 'Speech recognition could not complete. Try again.',
    retryable: true,
  },
  voice_turn_timeout: {
    code: 'voice_turn_timeout',
    message: 'That recording took too long. Try a shorter one.',
    retryable: true,
  },
};

const FALLBACK_ERROR: MappedTranscriptError = {
  code: 'stt_error',
  message: 'Speech recognition could not complete. Try again.',
  retryable: true,
};

export function mapTranscriptError(
  code: string | null | undefined,
): MappedTranscriptError {
  const normalized = typeof code === 'string' ? code.trim().toLowerCase() : '';
  return TRANSCRIPT_ERRORS[normalized] ?? FALLBACK_ERROR;
}

export function createTranscriptPlaceholder(
  messages: VoiceTranscriptMessage[],
  turnId: string,
  responseId: string | null,
  now: number,
): VoiceTranscriptMessage[] {
  if (messages.some(message => message.turnId === turnId)) {
    return messages;
  }

  return [
    ...messages,
    {
      id: `user-${turnId}`,
      turnId,
      responseId,
      text: '',
      status: 'listening' as const,
      final: false,
      transcriptSequence: null,
      audioDurationMs: null,
      language: null,
      startedAtMs: now,
      speechEndedAtMs: null,
      finalReceivedAtMs: null,
      uiSpeechEndToFinalMs: null,
      serverSpeechEndToFinalMs: null,
      errorCode: null,
    },
  ].slice(-MAX_MESSAGES);
}

export function markTranscriptPhase(
  messages: VoiceTranscriptMessage[],
  turnId: string | null,
  phase: 'listening' | 'speech_detected' | 'transcribing',
  now: number,
): VoiceTranscriptMessage[] {
  if (!turnId) {
    return messages;
  }

  return messages.map(message => {
    if (
      message.turnId !== turnId ||
      message.final ||
      message.status === 'cancelled' ||
      message.status === 'error'
    ) {
      return message;
    }
    return {
      ...message,
      status: phase,
      speechEndedAtMs:
        phase === 'transcribing'
          ? message.speechEndedAtMs ?? now
          : message.speechEndedAtMs,
    };
  });
}

export function markTranscriptSpeechEnded(
  messages: VoiceTranscriptMessage[],
  turnId: string | null,
  now: number,
): VoiceTranscriptMessage[] {
  if (!turnId) {
    return messages;
  }
  return messages.map(message =>
    message.turnId === turnId && !message.final
      ? { ...message, speechEndedAtMs: message.speechEndedAtMs ?? now }
      : message,
  );
}

export function markTranscriptError(
  messages: VoiceTranscriptMessage[],
  turnId: string | null,
  code: string | null | undefined,
): VoiceTranscriptMessage[] {
  if (!turnId) {
    return messages;
  }
  const mapped = mapTranscriptError(code);
  return messages.map(message =>
    message.turnId === turnId && !message.final
      ? { ...message, status: 'error', errorCode: mapped.code }
      : message,
  );
}

export function markTranscriptCancelled(
  messages: VoiceTranscriptMessage[],
  turnId: string | null,
): VoiceTranscriptMessage[] {
  if (!turnId) {
    return messages;
  }
  return messages.map(message =>
    message.turnId === turnId && !message.final
      ? { ...message, status: 'cancelled', errorCode: 'stt_cancelled' }
      : message,
  );
}

export function applyTranscriptEvent(
  messages: VoiceTranscriptMessage[],
  event: CanonicalTranscriptEvent,
  now: number,
): { messages: VoiceTranscriptMessage[]; accepted: boolean } {
  if (!event.turnId) {
    return { messages, accepted: false };
  }

  const existing = messages.find(message => message.turnId === event.turnId);
  const withPlaceholder = existing
    ? messages
    : createTranscriptPlaceholder(
        messages,
        event.turnId,
        event.responseId,
        now,
      );
  const current = withPlaceholder.find(
    message => message.turnId === event.turnId,
  );
  if (
    !current ||
    current.status === 'cancelled' ||
    current.final ||
    (event.sequence !== null &&
      current.transcriptSequence !== null &&
      event.sequence <= current.transcriptSequence)
  ) {
    return { messages, accepted: false };
  }

  const text = event.text.trim();
  if (event.kind === 'partial' && !text) {
    return { messages, accepted: false };
  }

  if (event.kind === 'final' && !text) {
    return {
      messages: markTranscriptError(
        withPlaceholder,
        event.turnId,
        'stt_empty_transcript',
      ),
      accepted: true,
    };
  }

  const nextMessages = withPlaceholder.map(message => {
    if (message.turnId !== event.turnId) {
      return message;
    }
    const finalReceivedAtMs =
      event.kind === 'final' ? now : message.finalReceivedAtMs;
    const speechEndToFinal =
      event.kind === 'final' && message.speechEndedAtMs !== null
        ? Math.max(0, now - message.speechEndedAtMs)
        : message.uiSpeechEndToFinalMs;
    return {
      ...message,
      responseId: event.responseId ?? message.responseId,
      text,
      status: event.kind,
      final: event.kind === 'final',
      transcriptSequence: event.sequence,
      audioDurationMs: event.audioDurationMs ?? message.audioDurationMs,
      language: event.language ?? message.language,
      finalReceivedAtMs,
      uiSpeechEndToFinalMs: speechEndToFinal,
      serverSpeechEndToFinalMs: metricNumber(
        event.metrics,
        'speech_end_to_final_transcript_ms',
      ),
      errorCode: null,
    };
  });
  return { messages: nextMessages, accepted: true };
}

function metricNumber(
  metrics: VoiceTranscriptMetrics,
  name: string,
): number | null {
  const value = metrics[name];
  return typeof value === 'number' && Number.isFinite(value) ? value : null;
}
