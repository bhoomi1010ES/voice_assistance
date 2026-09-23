import {
  createConversationState,
  mapConversationError,
  reduceVoiceEvent,
  ConversationState,
  VoiceEvent,
} from '../src/voice/conversation';
import { normalizeVoiceGatewayEvent } from '../src/voice/VoiceSocket';

const BASE = {
  sessionId: 'session-1',
  turnId: 'turn-1',
  responseId: 'response-1',
  timestampMs: 1,
};

function reduce(
  state: ConversationState,
  event: VoiceEvent,
  now = 1,
): ConversationState {
  const result = reduceVoiceEvent(state, event, now);
  expect(result.accepted).toBe(true);
  return result.state;
}

function userFinal(state = createConversationState()): ConversationState {
  return reduce(state, {
    ...BASE,
    type: 'user.transcript.final',
    text: 'What is the weather today?',
    sequence: 1,
  });
}

function responseStarted(
  state: ConversationState,
  responseId = 'response-1',
  retry = false,
): ConversationState {
  return reduce(state, {
    ...BASE,
    responseId,
    type: 'assistant.request.started',
    sequence: null,
    retry,
  });
}

test('commits the final user transcript before ordered assistant streaming', () => {
  let state = responseStarted(userFinal());

  for (let sequence = 1; sequence <= 250; sequence += 1) {
    state = reduce(state, {
      ...BASE,
      type: 'assistant.text.delta',
      delta: sequence === 1 ? 'word ' : `${sequence} `,
      sequence,
    });
  }

  const user = state.messages.find(message => message.role === 'user');
  const assistant = state.messages.find(
    message => message.role === 'assistant',
  );
  expect(user).toMatchObject({
    turnId: 'turn-1',
    text: 'What is the weather today?',
    final: true,
    status: 'final',
  });
  expect(assistant).toMatchObject({
    responseId: 'response-1',
    status: 'streaming',
    firstTextAtMs: expect.any(Number),
  });
  expect(assistant?.role === 'assistant' ? assistant.text : '').toMatch(
    /^word 2 3 4 .* 250 $/,
  );
  expect(state.messages).toHaveLength(2);
});

test('rejects duplicate and out-of-order deltas, including after completion', () => {
  let state = responseStarted(userFinal());
  state = reduce(state, {
    ...BASE,
    type: 'assistant.text.delta',
    delta: 'Hello ',
    sequence: 2,
  });
  const duplicate = reduceVoiceEvent(
    state,
    { ...BASE, type: 'assistant.text.delta', delta: 'bad', sequence: 2 },
    3,
  );
  const old = reduceVoiceEvent(
    state,
    { ...BASE, type: 'assistant.text.delta', delta: 'old', sequence: 1 },
    4,
  );
  expect(duplicate.accepted).toBe(false);
  expect(old.accepted).toBe(false);

  state = reduce(state, {
    ...BASE,
    type: 'assistant.text.completed',
    text: 'Hello there',
    sequence: 3,
    metrics: {},
  });
  const late = reduceVoiceEvent(
    state,
    { ...BASE, type: 'assistant.text.delta', delta: ' late', sequence: 4 },
    5,
  );
  expect(late.accepted).toBe(false);
  expect(late.state.messages).toEqual(state.messages);
});

test('cancellation rejects late response data and retry uses a new response identity', () => {
  let state = responseStarted(userFinal());
  state = reduce(state, {
    ...BASE,
    type: 'assistant.response.cancelled',
  });
  const lateDelta = reduceVoiceEvent(
    state,
    { ...BASE, type: 'assistant.text.delta', delta: 'late', sequence: 1 },
    2,
  );
  expect(lateDelta.accepted).toBe(false);

  state = reduce(state, {
    ...BASE,
    type: 'assistant.request.started',
    responseId: 'response-2',
    sequence: null,
    retry: true,
  });
  state = reduce(state, {
    ...BASE,
    responseId: 'response-2',
    type: 'assistant.response.failed',
    errorCode: 'llm_timeout',
    retryable: true,
  });
  // A failed response is retriable without duplicating the user message.
  const retryState = reduceVoiceEvent(
    state,
    {
      ...BASE,
      responseId: 'response-3',
      type: 'assistant.request.started',
      sequence: null,
      retry: true,
    },
    4,
  );
  expect(retryState.accepted).toBe(true);
  expect(
    retryState.state.messages.filter(message => message.role === 'user'),
  ).toHaveLength(1);
  expect(
    retryState.state.messages
      .filter(message => message.role === 'assistant')
      .map(message => message.responseId),
  ).toEqual(['response-1', 'response-2', 'response-3']);
});

test('maps backend failures to safe user copy and rejects unauthorized tool state', () => {
  expect(mapConversationError('llm_context_limit')).toMatchObject({
    message: expect.stringMatching(/new conversation/i),
    retryable: false,
  });
  expect(mapConversationError('provider-secret-error').message).not.toMatch(
    /provider-secret-error/i,
  );

  const result = reduceVoiceEvent(
    createConversationState(),
    {
      ...BASE,
      type: 'tool.status',
      toolCallId: 'tool-1',
      name: 'private_tool',
      status: 'success',
      result: 'private result',
      confirmationId: null,
      errorCode: null,
    },
    1,
  );
  expect(result.accepted).toBe(false);
  expect(result.state.messages).toHaveLength(0);
});

test.each(['get_current_time', 'get_current_date'])(
  'accepts only ordered read-only %s tool lifecycle transitions',
  name => {
    let state = createConversationState();
    state = reduce(state, {
      ...BASE,
      type: 'tool.status',
      toolCallId: 'tool-time-1',
      name,
      status: 'understanding',
      result: null,
      confirmationId: null,
      errorCode: null,
    });
    state = reduce(state, {
      ...BASE,
      type: 'tool.status',
      toolCallId: 'tool-time-1',
      name,
      status: 'executing',
      result: null,
      confirmationId: null,
      errorCode: null,
    });
    state = reduce(state, {
      ...BASE,
      type: 'tool.status',
      toolCallId: 'tool-time-1',
      name,
      status: 'success',
      result: null,
      confirmationId: null,
      errorCode: null,
    });

    const duplicateSuccess = reduceVoiceEvent(
      state,
      {
        ...BASE,
        type: 'tool.status',
        toolCallId: 'tool-time-1',
        name,
        status: 'success',
        result: null,
        confirmationId: null,
        errorCode: null,
      },
      2,
    );
    expect(duplicateSuccess.accepted).toBe(false);
    expect(state.messages[state.messages.length - 1]).toMatchObject({
      role: 'tool',
      status: 'success',
    });
  },
);

test('requires confirmation before a mutating tool can execute or succeed', () => {
  const understanding: VoiceEvent = {
    ...BASE,
    type: 'tool.status',
    toolCallId: 'tool-task-1',
    name: 'create_task',
    status: 'understanding',
    result: null,
    confirmationId: null,
    errorCode: null,
  };
  let state = reduce(createConversationState(), understanding);

  const prematureExecution = reduceVoiceEvent(
    state,
    { ...understanding, status: 'executing' },
    2,
  );
  const prematureSuccess = reduceVoiceEvent(
    state,
    { ...understanding, status: 'success' },
    2,
  );
  expect(prematureExecution.accepted).toBe(false);
  expect(prematureSuccess.accepted).toBe(false);

  state = reduce(state, {
    ...understanding,
    status: 'confirmation_required',
    confirmationId: 'confirmation-1',
  });
  state = reduce(state, { ...understanding, status: 'approved' });
  state = reduce(state, { ...understanding, status: 'executing' });
  state = reduce(state, { ...understanding, status: 'success' });
  expect(state.messages[state.messages.length - 1]).toMatchObject({
    role: 'tool',
    status: 'success',
    confirmationId: 'confirmation-1',
  });
});

test.each(['memory_save', 'memory_forget'])(
  '%s requires confirmation before execution and reaches success once',
  name => {
    const understanding: VoiceEvent = {
      ...BASE,
      type: 'tool.status',
      toolCallId: `tool-${name}-1`,
      name,
      status: 'understanding',
      result: null,
      confirmationId: null,
      errorCode: null,
    };
    let state = reduce(createConversationState(), understanding);

    expect(
      reduceVoiceEvent(state, { ...understanding, status: 'executing' }, 2)
        .accepted,
    ).toBe(false);
    expect(
      reduceVoiceEvent(state, { ...understanding, status: 'success' }, 2)
        .accepted,
    ).toBe(false);

    state = reduce(state, {
      ...understanding,
      status: 'confirmation_required',
      confirmationId: 'memory-confirmation-1',
    });
    state = reduce(state, { ...understanding, status: 'approved' });
    state = reduce(state, { ...understanding, status: 'executing' });
    state = reduce(state, { ...understanding, status: 'success' });

    expect(state.messages[state.messages.length - 1]).toMatchObject({
      role: 'tool',
      name,
      status: 'success',
      confirmationId: 'memory-confirmation-1',
    });
  },
);

test('memory search remains read-only and cannot enter confirmation states', () => {
  const understanding: VoiceEvent = {
    ...BASE,
    type: 'tool.status',
    toolCallId: 'tool-memory-search-1',
    name: 'memory_search',
    status: 'understanding',
    result: null,
    confirmationId: null,
    errorCode: null,
  };
  const state = reduce(createConversationState(), understanding);

  expect(
    reduceVoiceEvent(
      state,
      { ...understanding, status: 'confirmation_required' },
      2,
    ).accepted,
  ).toBe(false);
  expect(
    reduceVoiceEvent(state, { ...understanding, status: 'executing' }, 2)
      .accepted,
  ).toBe(true);
});

test('normalizes server-owned tool events without accepting raw result data', () => {
  expect(
    normalizeVoiceGatewayEvent({
      type: 'tool.status',
      session_id: 'session-1',
      turn_id: 'turn-1',
      response_id: 'response-1',
      tool_call_id: 'tool-time-1',
      tool_name: 'get_current_time',
      tool_status: 'executing',
      result: 'private server payload',
    }),
  ).toMatchObject({
    type: 'tool.status',
    assistant: {
      type: 'tool.status',
      toolCallId: 'tool-time-1',
      name: 'get_current_time',
      status: 'executing',
      result: null,
    },
  });
});

test('accepts bounded assistant events and does not expose provider fields to the canonical event', () => {
  expect(
    normalizeVoiceGatewayEvent({
      event: 'assistant.text.delta',
      session_id: 'session-1',
      turn_id: 'turn-1',
      response_id: 'response-1',
      sequence: 4,
      delta: 'safe text',
      provider: 'private-provider',
      model: 'private-model',
    }),
  ).toMatchObject({
    type: 'assistant.text.delta',
    assistant: {
      type: 'assistant.text.delta',
      delta: 'safe text',
      sequence: 4,
    },
  });
  expect(
    normalizeVoiceGatewayEvent({
      event: 'assistant.text.delta',
      session_id: 'session-1',
      turn_id: 'turn-1',
      response_id: 'response-1',
      sequence: 4,
      delta: 'x'.repeat(16 * 1024),
    }),
  ).toBeNull();
});
