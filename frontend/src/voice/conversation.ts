export type ConversationError = {
  code: string;
  message: string;
  retryable: boolean;
};

export type ConversationUserStatus =
  | 'listening'
  | 'speech_detected'
  | 'transcribing'
  | 'partial'
  | 'final'
  | 'error'
  | 'cancelled';

export type ConversationAssistantStatus =
  | 'pending'
  | 'streaming'
  | 'completed'
  | 'failed'
  | 'cancelled';

export type ConversationToolStatus =
  | 'understanding'
  | 'confirmation_required'
  | 'approved'
  | 'executing'
  | 'success'
  | 'failed'
  | 'cancelled';

export type ConversationUserMessage = {
  id: string;
  role: 'user';
  turnId: string;
  responseId: string | null;
  text: string;
  status: ConversationUserStatus;
  final: boolean;
  sequence: number | null;
  startedAtMs: number;
  finalReceivedAtMs: number | null;
};

export type ConversationAssistantMessage = {
  id: string;
  role: 'assistant';
  turnId: string;
  responseId: string;
  text: string;
  status: ConversationAssistantStatus;
  sequence: number | null;
  errorCode: string | null;
  retryable: boolean;
  startedAtMs: number;
  firstTextAtMs: number | null;
  completedAtMs: number | null;
  renderCompletedAtMs: number | null;
  metrics: Record<string, number | null>;
};

export type ConversationSystemMessage = {
  id: string;
  role: 'system';
  text: string;
  status: 'info' | 'error';
};

export type ConversationToolMessage = {
  id: string;
  role: 'tool';
  turnId: string;
  responseId: string;
  toolCallId: string;
  name: string;
  status: ConversationToolStatus;
  result: string | null;
  confirmationId: string | null;
  errorCode: string | null;
};

export type ConversationMessage =
  | ConversationUserMessage
  | ConversationAssistantMessage
  | ConversationSystemMessage
  | ConversationToolMessage;

type VoiceEventBase = {
  sessionId: string | null;
  turnId: string | null;
  responseId: string | null;
  timestampMs: number | null;
};

export type VoiceEvent =
  | (VoiceEventBase & {
      type: 'user.transcript.partial' | 'user.transcript.final';
      text: string;
      sequence: number | null;
    })
  | (VoiceEventBase & {
      type: 'assistant.request.started';
      sequence: number | null;
      retry: boolean;
    })
  | (VoiceEventBase & {
      type: 'assistant.text.delta';
      delta: string;
      sequence: number;
    })
  | (VoiceEventBase & {
      type: 'assistant.text.completed';
      text: string;
      sequence: number | null;
      metrics: Record<string, number | null>;
    })
  | (VoiceEventBase & {
      type: 'assistant.response.failed';
      errorCode: string;
      retryable: boolean;
    })
  | (VoiceEventBase & {
      type: 'assistant.response.cancelled';
    })
  | (VoiceEventBase & {
      type: 'turn.completed';
    })
  | (VoiceEventBase & {
      type: 'tool.status';
      toolCallId: string;
      name: string;
      status: ConversationToolStatus;
      result: string | null;
      confirmationId: string | null;
      errorCode: string | null;
    });

export type ConversationState = {
  messages: ConversationMessage[];
};

export const MAX_CONVERSATION_MESSAGES = 120;
export const TOOL_UI_ENABLED = true;

const MUTATING_TOOL_NAMES = new Set([
  'create_task',
  'memory_save',
  'memory_forget',
]);
const READ_ONLY_TOOL_NAMES = new Set(['get_current_time', 'memory_search']);
const SUPPORTED_TOOL_NAMES = new Set([
  ...MUTATING_TOOL_NAMES,
  ...READ_ONLY_TOOL_NAMES,
]);
const TOOL_STATUS_TRANSITIONS: Record<
  ConversationToolStatus,
  ReadonlySet<ConversationToolStatus>
> = {
  understanding: new Set([
    'confirmation_required',
    'executing',
    'failed',
    'cancelled',
  ]),
  confirmation_required: new Set(['approved', 'failed', 'cancelled']),
  approved: new Set(['executing', 'failed', 'cancelled']),
  executing: new Set(['success', 'failed', 'cancelled']),
  success: new Set(),
  failed: new Set(),
  cancelled: new Set(),
};

const CONVERSATION_ERRORS: Record<string, ConversationError> = {
  llm_configuration_error: {
    code: 'llm_configuration_error',
    message: 'The assistant is not configured yet. Contact support.',
    retryable: false,
  },
  llm_authentication_error: {
    code: 'llm_authentication_error',
    message: 'The assistant service is not authorized. Contact support.',
    retryable: false,
  },
  llm_permission_error: {
    code: 'llm_permission_error',
    message: 'The assistant service is not available for this account.',
    retryable: false,
  },
  llm_model_not_found: {
    code: 'llm_model_not_found',
    message: 'The assistant is temporarily unavailable. Try again later.',
    retryable: true,
  },
  llm_invalid_request: {
    code: 'llm_invalid_request',
    message: 'The assistant could not process that request. Try again.',
    retryable: true,
  },
  llm_rate_limited: {
    code: 'llm_rate_limited',
    message: 'The assistant is busy. Wait a moment and try again.',
    retryable: true,
  },
  llm_timeout: {
    code: 'llm_timeout',
    message: 'The assistant took too long to respond. Try again.',
    retryable: true,
  },
  llm_overloaded: {
    code: 'llm_overloaded',
    message: 'The assistant is busy right now. Try again in a moment.',
    retryable: true,
  },
  llm_provider_error: {
    code: 'llm_provider_error',
    message: 'The assistant could not respond. Try again.',
    retryable: true,
  },
  llm_protocol_error: {
    code: 'llm_protocol_error',
    message: 'The assistant returned an unexpected response. Try again.',
    retryable: true,
  },
  llm_cancelled: {
    code: 'llm_cancelled',
    message: 'The response was stopped.',
    retryable: true,
  },
  llm_context_limit: {
    code: 'llm_context_limit',
    message:
      'This conversation is too long. Start a new conversation to continue.',
    retryable: false,
  },
  llm_tool_not_authorized: {
    code: 'llm_tool_not_authorized',
    message: 'That assistant action is not available for this account.',
    retryable: false,
  },
  llm_tool_invalid_arguments: {
    code: 'llm_tool_invalid_arguments',
    message: 'The assistant could not complete that action. Try again.',
    retryable: true,
  },
  llm_tool_rate_limited: {
    code: 'llm_tool_rate_limited',
    message: 'That assistant action is temporarily busy. Try again later.',
    retryable: true,
  },
  llm_tool_execution_failed: {
    code: 'llm_tool_execution_failed',
    message: 'The assistant action could not be completed.',
    retryable: true,
  },
  llm_tool_confirmation_required: {
    code: 'llm_tool_confirmation_required',
    message: 'This assistant action needs confirmation before it can run.',
    retryable: false,
  },
  llm_tool_loop_limit: {
    code: 'llm_tool_loop_limit',
    message: 'The assistant could not finish that action. Try again.',
    retryable: true,
  },
  llm_empty_response: {
    code: 'llm_empty_response',
    message: 'The assistant returned no answer. Try again.',
    retryable: true,
  },
};

const FALLBACK_CONVERSATION_ERROR: ConversationError = {
  code: 'llm_provider_error',
  message: 'The assistant could not respond. Try again.',
  retryable: true,
};

export function mapConversationError(
  code: string | null | undefined,
): ConversationError {
  const normalized = typeof code === 'string' ? code.trim().toLowerCase() : '';
  return CONVERSATION_ERRORS[normalized] ?? FALLBACK_CONVERSATION_ERROR;
}

export function createConversationState(): ConversationState {
  return { messages: [] };
}

export function ensureConversationUserPlaceholder(
  state: ConversationState,
  turnId: string,
  responseId: string | null,
  now: number,
): ConversationState {
  const existing = state.messages.some(
    message => message.role === 'user' && message.turnId === turnId,
  );
  if (existing) {
    return state;
  }
  return replaceOrAppend(state, {
    id: `user-${turnId}`,
    role: 'user',
    turnId,
    responseId,
    text: '',
    status: 'listening',
    final: false,
    sequence: null,
    startedAtMs: now,
    finalReceivedAtMs: null,
  });
}

export function markConversationUserPhase(
  state: ConversationState,
  turnId: string | null,
  status: Extract<
    ConversationUserStatus,
    'listening' | 'speech_detected' | 'transcribing'
  >,
): ConversationState {
  if (!turnId) {
    return state;
  }
  let changed = false;
  const messages = state.messages.map(message => {
    if (message.role !== 'user' || message.turnId !== turnId || message.final) {
      return message;
    }
    changed = changed || message.status !== status;
    return { ...message, status };
  });
  return changed ? { messages } : state;
}

export function reduceVoiceEvent(
  state: ConversationState,
  event: VoiceEvent,
  now: number,
  options: { toolUiEnabled?: boolean } = {},
): { state: ConversationState; accepted: boolean } {
  if (
    (event.type !== 'tool.status' && !event.turnId) ||
    (event.type !== 'tool.status' &&
      event.type !== 'user.transcript.partial' &&
      event.type !== 'user.transcript.final' &&
      !event.responseId)
  ) {
    return { state, accepted: false };
  }

  if (event.type === 'tool.status') {
    if (
      !(options.toolUiEnabled ?? TOOL_UI_ENABLED) ||
      !event.turnId ||
      !event.responseId ||
      !SUPPORTED_TOOL_NAMES.has(event.name)
    ) {
      return { state, accepted: false };
    }
    return applyToolEvent(state, event);
  }

  if (
    event.type === 'user.transcript.partial' ||
    event.type === 'user.transcript.final'
  ) {
    return applyUserTranscript(state, event, now);
  }

  if (event.type === 'assistant.request.started') {
    return applyRequestStarted(state, event, now);
  }

  if (event.type === 'assistant.text.delta') {
    return applyTextDelta(state, event, now);
  }

  if (event.type === 'assistant.text.completed') {
    return applyTextCompleted(state, event, now);
  }

  if (event.type === 'assistant.response.failed') {
    return applyResponseFailed(state, event, now);
  }

  if (event.type === 'assistant.response.cancelled') {
    return applyResponseCancelled(state, event, now);
  }

  if (event.type !== 'turn.completed') {
    return { state, accepted: false };
  }
  return applyTurnCompleted(state, event, now);
}

export function markConversationRendered(
  state: ConversationState,
  responseId: string,
  now: number,
): ConversationState {
  let changed = false;
  const messages = state.messages.map(message => {
    if (
      message.role !== 'assistant' ||
      message.responseId !== responseId ||
      message.firstTextAtMs === null ||
      message.renderCompletedAtMs !== null
    ) {
      return message;
    }
    changed = true;
    return { ...message, renderCompletedAtMs: now };
  });
  return changed ? { messages } : state;
}

export function clearConversation(): ConversationState {
  return createConversationState();
}

function applyUserTranscript(
  state: ConversationState,
  event: Extract<
    VoiceEvent,
    { type: 'user.transcript.partial' | 'user.transcript.final' }
  >,
  now: number,
): { state: ConversationState; accepted: boolean } {
  const existing = state.messages.find(
    message => message.role === 'user' && message.turnId === event.turnId,
  ) as ConversationUserMessage | undefined;
  if (existing && (existing.final || existing.status === 'cancelled')) {
    return { state, accepted: false };
  }
  if (event.type === 'user.transcript.partial' && !event.text.trim()) {
    return { state, accepted: false };
  }
  if (
    existing &&
    event.sequence !== null &&
    existing.finalReceivedAtMs === null &&
    existing.sequence !== null &&
    event.sequence <= existing.sequence
  ) {
    return { state, accepted: false };
  }

  const next: ConversationUserMessage = existing
    ? {
        ...existing,
        responseId: event.responseId ?? existing.responseId,
        text: event.text.trim(),
        status: event.type === 'user.transcript.final' ? 'final' : 'partial',
        final: event.type === 'user.transcript.final',
        sequence: event.sequence,
        finalReceivedAtMs:
          event.type === 'user.transcript.final'
            ? now
            : existing.finalReceivedAtMs,
      }
    : {
        id: `user-${event.turnId}`,
        role: 'user',
        turnId: event.turnId!,
        responseId: event.responseId,
        text: event.text.trim(),
        status: event.type === 'user.transcript.final' ? 'final' : 'partial',
        final: event.type === 'user.transcript.final',
        sequence: event.sequence,
        startedAtMs: now,
        finalReceivedAtMs: event.type === 'user.transcript.final' ? now : null,
      };
  return { state: replaceOrAppend(state, next), accepted: true };
}

function applyRequestStarted(
  state: ConversationState,
  event: Extract<VoiceEvent, { type: 'assistant.request.started' }>,
  now: number,
): { state: ConversationState; accepted: boolean } {
  if (!event.turnId || !event.responseId) {
    return { state, accepted: false };
  }
  const existing = state.messages.find(
    message =>
      message.role === 'assistant' && message.responseId === event.responseId,
  ) as ConversationAssistantMessage | undefined;
  if (existing) {
    return {
      state:
        existing.status === 'pending' || existing.status === 'streaming'
          ? state
          : state,
      accepted:
        existing.status === 'pending' || existing.status === 'streaming',
    };
  }
  const activeForTurn = state.messages.find(
    message =>
      message.role === 'assistant' &&
      message.turnId === event.turnId &&
      (message.status === 'pending' || message.status === 'streaming'),
  );
  if (activeForTurn) {
    return { state, accepted: false };
  }
  const next: ConversationAssistantMessage = {
    id: `assistant-${event.responseId}`,
    role: 'assistant',
    turnId: event.turnId,
    responseId: event.responseId,
    text: '',
    status: 'pending',
    sequence: event.sequence,
    errorCode: null,
    retryable: true,
    startedAtMs: now,
    firstTextAtMs: null,
    completedAtMs: null,
    renderCompletedAtMs: null,
    metrics: {},
  };
  return { state: replaceOrAppend(state, next), accepted: true };
}

function applyTextDelta(
  state: ConversationState,
  event: Extract<VoiceEvent, { type: 'assistant.text.delta' }>,
  now: number,
): { state: ConversationState; accepted: boolean } {
  if (!event.turnId || !event.responseId || !event.delta) {
    return { state, accepted: false };
  }
  const existing = state.messages.find(
    message =>
      message.role === 'assistant' && message.responseId === event.responseId,
  ) as ConversationAssistantMessage | undefined;
  if (
    !existing ||
    (existing.status !== 'pending' && existing.status !== 'streaming') ||
    (existing.sequence !== null && event.sequence <= existing.sequence)
  ) {
    return { state, accepted: false };
  }
  const next: ConversationAssistantMessage = {
    ...existing,
    text: existing.text + event.delta,
    status: 'streaming',
    sequence: event.sequence,
    firstTextAtMs: existing.firstTextAtMs ?? now,
  };
  return { state: replaceOrAppend(state, next), accepted: true };
}

function applyTextCompleted(
  state: ConversationState,
  event: Extract<VoiceEvent, { type: 'assistant.text.completed' }>,
  now: number,
): { state: ConversationState; accepted: boolean } {
  if (!event.turnId || !event.responseId || !event.text.trim()) {
    return { state, accepted: false };
  }
  const existing = state.messages.find(
    message =>
      message.role === 'assistant' && message.responseId === event.responseId,
  ) as ConversationAssistantMessage | undefined;
  if (
    !existing ||
    existing.status === 'failed' ||
    existing.status === 'cancelled' ||
    existing.status === 'completed'
  ) {
    return { state, accepted: false };
  }
  const next: ConversationAssistantMessage = {
    ...existing,
    text: event.text.trim(),
    status: 'completed',
    sequence: event.sequence ?? existing.sequence,
    completedAtMs: now,
    firstTextAtMs: existing.firstTextAtMs ?? now,
    metrics: event.metrics,
  };
  return { state: replaceOrAppend(state, next), accepted: true };
}

function applyResponseFailed(
  state: ConversationState,
  event: Extract<VoiceEvent, { type: 'assistant.response.failed' }>,
  now: number,
): { state: ConversationState; accepted: boolean } {
  if (!event.turnId || !event.responseId) {
    return { state, accepted: false };
  }
  const existing = state.messages.find(
    message =>
      message.role === 'assistant' && message.responseId === event.responseId,
  ) as ConversationAssistantMessage | undefined;
  if (
    !existing ||
    existing.status === 'cancelled' ||
    existing.status === 'completed'
  ) {
    return { state, accepted: false };
  }
  const next: ConversationAssistantMessage = {
    ...existing,
    status: 'failed',
    errorCode: mapConversationError(event.errorCode).code,
    retryable: event.retryable,
    completedAtMs: now,
  };
  return { state: replaceOrAppend(state, next), accepted: true };
}

function applyResponseCancelled(
  state: ConversationState,
  event: Extract<VoiceEvent, { type: 'assistant.response.cancelled' }>,
  now: number,
): { state: ConversationState; accepted: boolean } {
  if (!event.turnId || !event.responseId) {
    return { state, accepted: false };
  }
  const existing = state.messages.find(
    message =>
      message.role === 'assistant' && message.responseId === event.responseId,
  ) as ConversationAssistantMessage | undefined;
  if (
    !existing ||
    existing.status === 'completed' ||
    existing.status === 'failed'
  ) {
    return { state, accepted: false };
  }
  const next: ConversationAssistantMessage = {
    ...existing,
    status: 'cancelled',
    errorCode: 'llm_cancelled',
    retryable: true,
    completedAtMs: now,
  };
  return { state: replaceOrAppend(state, next), accepted: true };
}

function applyTurnCompleted(
  state: ConversationState,
  event: Extract<VoiceEvent, { type: 'turn.completed' }>,
  now: number,
): { state: ConversationState; accepted: boolean } {
  if (!event.turnId || !event.responseId) {
    return { state, accepted: false };
  }
  const existing = state.messages.find(
    message =>
      message.role === 'assistant' && message.responseId === event.responseId,
  ) as ConversationAssistantMessage | undefined;
  if (
    !existing ||
    existing.status === 'failed' ||
    existing.status === 'cancelled'
  ) {
    return { state, accepted: false };
  }
  if (!existing.text.trim()) {
    return {
      state: {
        messages: state.messages.filter(message => message.id !== existing.id),
      },
      accepted: true,
    };
  }
  const next: ConversationAssistantMessage = {
    ...existing,
    status: 'completed',
    completedAtMs: existing.completedAtMs ?? now,
  };
  return { state: replaceOrAppend(state, next), accepted: true };
}

function applyToolEvent(
  state: ConversationState,
  event: Extract<VoiceEvent, { type: 'tool.status' }>,
): { state: ConversationState; accepted: boolean } {
  const id = `tool-${event.toolCallId}`;
  const existing = state.messages.find(
    message => message.role === 'tool' && message.id === id,
  ) as ConversationToolMessage | undefined;
  if (!existing && event.status !== 'understanding') {
    return { state, accepted: false };
  }
  if (
    existing &&
    (existing.name !== event.name ||
      !TOOL_STATUS_TRANSITIONS[existing.status].has(event.status) ||
      (MUTATING_TOOL_NAMES.has(existing.name) &&
        existing.status === 'understanding' &&
        event.status === 'executing') ||
      (READ_ONLY_TOOL_NAMES.has(existing.name) &&
        ['confirmation_required', 'approved'].includes(event.status)))
  ) {
    return { state, accepted: false };
  }
  const next: ConversationToolMessage = existing
    ? {
        ...existing,
        status: event.status,
        result: event.result,
        confirmationId: event.confirmationId ?? existing.confirmationId,
        errorCode: event.errorCode,
      }
    : {
        id,
        role: 'tool',
        turnId: event.turnId!,
        responseId: event.responseId!,
        toolCallId: event.toolCallId,
        name: event.name,
        status: event.status,
        result: event.result,
        confirmationId: event.confirmationId,
        errorCode: event.errorCode,
      };
  return { state: replaceOrAppend(state, next), accepted: true };
}

function replaceOrAppend(
  state: ConversationState,
  next: ConversationMessage,
): ConversationState {
  const index = state.messages.findIndex(message => message.id === next.id);
  const messages = [...state.messages];
  if (index === -1) {
    messages.push(next);
  } else {
    messages[index] = next;
  }
  return { messages: messages.slice(-MAX_CONVERSATION_MESSAGES) };
}
