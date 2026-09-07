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

test('maps backend failures to safe user copy and keeps tool UI disabled', () => {
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
      status: 'completed',
      result: 'private result',
    },
    1,
  );
  expect(result.accepted).toBe(false);
  expect(result.state.messages).toHaveLength(0);
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
