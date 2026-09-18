import { AppState, AppStateStatus } from 'react-native';
import {
  abortAllVoiceResponses,
  cancelVoiceResponse,
  commitVoiceAudio,
  connectVoiceGateway,
  disconnectVoiceGateway,
  endVoiceSession,
  getVoiceGatewayStatus,
  requestMicrophonePermission,
  resolveVoiceConfirmation,
  retryVoiceResponse,
  resetVoiceConversation,
  stopVoicePlayback,
  startMicrophone,
  startVoiceSession,
  startVoiceTurn,
  stopMicrophone,
  subscribeVoiceGatewayEvent,
  subscribeVoiceGatewayStatus,
  subscribeVoiceVadEvent,
  VoiceGatewayEvent,
  VoiceGatewayStatus,
} from '../native/VoiceModule';
import { publicApiConfig } from '../config/environment';
import {
  applyTranscriptEvent,
  CanonicalTranscriptEvent,
  createTranscriptPlaceholder,
  markTranscriptCancelled,
  markTranscriptError,
  markTranscriptPhase,
  markTranscriptSpeechEnded,
  mapTranscriptError,
  MappedTranscriptError,
  VoiceTranscriptMessage,
} from './transcript';
import {
  clearConversation,
  createConversationState,
  ConversationAssistantMessage,
  ConversationMessage,
  ConversationState,
  ensureConversationUserPlaceholder,
  mapConversationError,
  markConversationUserPhase,
  markConversationRendered as markConversationRenderedState,
  reduceVoiceEvent,
  VoiceEvent,
} from './conversation';
import {
  evaluatePlaybackBargeIn,
  PLAYBACK_BARGE_IN_CONFIRMATION_MS,
  PLAYBACK_ECHO_SIMILARITY_THRESHOLD,
  PLAYBACK_SPEECH_PROBABILITY_THRESHOLD,
  PLAYBACK_START_GUARD_MS,
} from './playbackBargeInPolicy';
import { emitLatencyTrace } from './latencyTrace';

export const VOICE_GATEWAY_URL = `${publicApiConfig.websocketBaseUrl}/v1/voice`;

// Native Silero VAD confirms silence after 320 ms. Keep the turn open a little
// longer so a normal pause inside a sentence can be followed by more speech.
// A new speech-start event cancels this timer and preserves the same STT turn.
export const SPEECH_END_COMMIT_GRACE_MS = 900;

export const VOICE_SERVER_EVENT_TYPES = [
  'voice.connection.opened',
  'voice.connection.closed',
  'voice.session.started',
  'voice.session.stale.reaped',
  'server.session.ready',
  'server.conversation.reset',
  'server.session.ending',
  'server.session.ended',
  'voice.turn.started',
  'server.turn.ready',
  'server.turn.failed',
  'server.turn.completed',
  'voice.pcm.accepted',
  'voice.audio.commit.received',
  'voice.turn.finalization.started',
  'transcript.partial',
  'transcript.final',
  'voice.transcript.partial',
  'voice.transcript.final.delivered',
  'assistant.response.started',
  'assistant.request.started',
  'assistant.thinking',
  'assistant.text.delta',
  'assistant.text.final',
  'assistant.response.failed',
  'llm.response.failed',
  'llm.response.completed',
  'tts.started',
  'tts.completed',
  'tts.cancelled',
  'tts.failed',
  'tts.playback.started',
  'tts.playback.completed',
  'tts.playback.stopped',
  'voice.confirmation.required',
  'confirmation.required',
  'confirmation.resolved',
  'tool.status',
  'response.cancelled',
  'server.pong',
  'server.error',
] as const;

export type VoiceServerEventType = (typeof VOICE_SERVER_EVENT_TYPES)[number];

export type VoiceConnectionState =
  | 'disconnected'
  | 'connecting'
  | 'connected'
  | 'reconnecting'
  | 'degraded'
  | 'failed';

export type VoiceSessionState = 'idle' | 'starting' | 'ready' | 'ending';

export type VoiceTurnState =
  | 'idle'
  | 'starting'
  | 'recording'
  | 'speech_detected'
  | 'committing'
  | 'waiting'
  | 'cancelled'
  | 'completed'
  | 'failed';

export type VoiceHeartbeatState = 'unknown' | 'healthy' | 'missed';

export type VoiceTtsPlaybackState =
  | 'idle'
  | 'buffering'
  | 'speaking'
  | 'stopping'
  | 'completed'
  | 'failed';

export type VoiceSocketSnapshot = {
  connection: VoiceConnectionState;
  session: VoiceSessionState;
  turn: VoiceTurnState;
  heartbeat: VoiceHeartbeatState;
  sessionId: string | null;
  turnId: string | null;
  responseId: string | null;
  ttsPlaybackState: VoiceTtsPlaybackState;
  ttsResponseId: string | null;
  ttsError: string | null;
  waitPhrase: string | null;
  continuousListening: boolean;
  followUpQueued: boolean;
  confirmationAwaitingVoice: boolean;
  speechDetected: boolean;
  transcriptMessages: VoiceTranscriptMessage[];
  conversationMessages: ConversationMessage[];
  transcriptError: MappedTranscriptError | null;
  firstTextAtMs: number | null;
  conversationRenderCompletedAtMs: number | null;
  eventSequence: number;
  lastEvent: VoiceServerEventType | null;
  lastEventAtMs: number | null;
  lastHeartbeatAtMs: number | null;
  reconnectAttempt: number;
  droppedEventCount: number;
  invalidEventCount: number;
  error: string | null;
};

export type VoiceSocketListener = (snapshot: VoiceSocketSnapshot) => void;

export type VoiceAppStateSource = {
  currentState?: AppStateStatus | null;
  addEventListener: (
    type: 'change',
    listener: (nextState: AppStateStatus) => void,
  ) => { remove: () => void };
};

export type VoiceSocketAdapter = {
  connect: (url: string) => Promise<VoiceGatewayStatus>;
  disconnect: () => Promise<VoiceGatewayStatus>;
  startSession: (
    resumeSessionId?: string | null,
  ) => Promise<VoiceGatewayStatus>;
  startTurn: (
    clientTurnId?: string | null,
    includePreRoll?: boolean,
  ) => Promise<VoiceGatewayStatus>;
  commitAudio: (durationMs: number) => Promise<VoiceGatewayStatus>;
  cancelResponse: (reason?: string | null) => Promise<VoiceGatewayStatus>;
  abortAll?: (reason?: string | null) => Promise<VoiceGatewayStatus>;
  resetConversation?: () => Promise<VoiceGatewayStatus>;
  stopPlayback?: () => Promise<VoiceGatewayStatus>;
  retryResponse?: (
    turnId: string,
    originalResponseId: string,
    transcript: string,
  ) => Promise<VoiceGatewayStatus>;
  resolveConfirmation?: (
    confirmationId: string,
    toolCallId: string,
    decision: 'approve' | 'deny',
  ) => Promise<VoiceGatewayStatus>;
  endSession: (reason?: string | null) => Promise<VoiceGatewayStatus>;
  getStatus: () => Promise<VoiceGatewayStatus>;
  startMicrophone?: () => Promise<unknown>;
  stopMicrophone?: () => Promise<unknown>;
  requestMicrophonePermission?: () => Promise<string>;
  subscribeVad?: (listener: (event: unknown) => void) => () => void;
  subscribeStatus: (
    listener: (status: VoiceGatewayStatus) => void,
  ) => () => void;
  subscribeEvent: (listener: (event: VoiceGatewayEvent) => void) => () => void;
};

export type VoiceSocketOptions = {
  adapter?: VoiceSocketAdapter;
  url?: string;
  /**
   * Refreshes/validates the authenticated HTTP session before opening a
   * socket. The callback must not expose or return credentials.
   */
  prepareConnection?: () => Promise<void>;
  appState?: VoiceAppStateSource;
  now?: () => number;
  connectTimeoutMs?: number;
  heartbeatTimeoutMs?: number;
  heartbeatCheckIntervalMs?: number;
  reconnectDelaysMs?: number[];
  continuousListening?: boolean;
  autoStartSession?: boolean;
};

export type VoiceTurnStartOptions = {
  preserveMicrophone?: boolean;
  includePreRoll?: boolean;
  autoCommitOnSpeechEnd?: boolean;
};

const INITIAL_SNAPSHOT: VoiceSocketSnapshot = {
  connection: 'disconnected',
  session: 'idle',
  turn: 'idle',
  heartbeat: 'unknown',
  sessionId: null,
  turnId: null,
  responseId: null,
  ttsPlaybackState: 'idle',
  ttsResponseId: null,
  ttsError: null,
  waitPhrase: null,
  continuousListening: true,
  followUpQueued: false,
  confirmationAwaitingVoice: false,
  speechDetected: false,
  transcriptMessages: [],
  conversationMessages: [],
  transcriptError: null,
  firstTextAtMs: null,
  conversationRenderCompletedAtMs: null,
  eventSequence: 0,
  lastEvent: null,
  lastEventAtMs: null,
  lastHeartbeatAtMs: null,
  reconnectAttempt: 0,
  droppedEventCount: 0,
  invalidEventCount: 0,
  error: null,
};

const DEFAULT_CONNECT_TIMEOUT_MS = 10_000;
const DEFAULT_HEARTBEAT_TIMEOUT_MS = 45_000;
const DEFAULT_HEARTBEAT_CHECK_INTERVAL_MS = 1_000;
const DEFAULT_RECONNECT_DELAYS_MS = [500, 1_000, 2_000];
const MAX_TRACKED_EVENT_IDS = 128;
const MAX_EVENT_BYTES = 16 * 1024;
const MAX_ID_LENGTH = 128;
const RESPONSE_SCOPED_EVENTS = new Set<VoiceServerEventType>([
  'transcript.partial',
  'transcript.final',
  'voice.transcript.partial',
  'voice.transcript.final.delivered',
  'assistant.response.started',
  'assistant.request.started',
  'assistant.thinking',
  'assistant.text.delta',
  'assistant.text.final',
  'server.turn.failed',
  'server.turn.completed',
  'assistant.response.failed',
  'llm.response.failed',
  'llm.response.completed',
  'tts.started',
  'tts.completed',
  'tts.cancelled',
  'tts.failed',
  'tts.playback.started',
  'tts.playback.completed',
  'tts.playback.stopped',
  'voice.confirmation.required',
  'confirmation.required',
  'confirmation.resolved',
  'tool.status',
  'response.cancelled',
]);

const TURN_SCOPED_EVENTS = new Set<VoiceServerEventType>([
  'voice.turn.started',
  'server.turn.ready',
  'server.turn.failed',
  'server.turn.completed',
  'voice.pcm.accepted',
  'voice.audio.commit.received',
  'voice.turn.finalization.started',
  'transcript.partial',
  'transcript.final',
  'voice.transcript.partial',
  'voice.transcript.final.delivered',
  'assistant.response.started',
  'assistant.request.started',
  'assistant.thinking',
  'assistant.text.delta',
  'assistant.text.final',
  'assistant.response.failed',
  'llm.response.failed',
  'llm.response.completed',
  'voice.confirmation.required',
  'confirmation.required',
  'confirmation.resolved',
  'tool.status',
  'response.cancelled',
]);

const KNOWN_EVENT_TYPES = new Set<string>(VOICE_SERVER_EVENT_TYPES);
const CLIENT_CLOCK_EVENT_TYPES = new Set<VoiceServerEventType>([
  'tts.playback.started',
  'tts.playback.completed',
  'tts.playback.stopped',
]);
const EVENTS_ALLOWED_DURING_CONNECTION_TRANSITION =
  new Set<VoiceServerEventType>([
    'server.error',
    'server.session.ended',
    'voice.session.stale.reaped',
  ]);
const TERMINAL_SESSION_ERROR_CODES = new Set(['session_not_available']);
const AUTH_EXPIRY_ERROR_CODES = new Set([
  'authentication_expired_or_revoked',
  'invalid_or_revoked_token',
]);

const nativeVoiceSocketAdapter: VoiceSocketAdapter = {
  connect: connectVoiceGateway,
  disconnect: disconnectVoiceGateway,
  startSession: startVoiceSession,
  startTurn: startVoiceTurn,
  commitAudio: commitVoiceAudio,
  cancelResponse: cancelVoiceResponse,
  abortAll: abortAllVoiceResponses,
  resetConversation: resetVoiceConversation,
  stopPlayback: stopVoicePlayback,
  retryResponse: retryVoiceResponse,
  resolveConfirmation: resolveVoiceConfirmation,
  endSession: endVoiceSession,
  getStatus: getVoiceGatewayStatus,
  startMicrophone,
  stopMicrophone,
  requestMicrophonePermission,
  subscribeStatus: subscribeVoiceGatewayStatus,
  subscribeEvent: subscribeVoiceGatewayEvent,
  subscribeVad: subscribeVoiceVadEvent,
};

export type NormalizedVoiceEvent = {
  type: VoiceServerEventType;
  eventId: string | null;
  sessionId: string | null;
  turnId: string | null;
  responseId: string | null;
  timestampMs: number | null;
  transcript?: CanonicalTranscriptEvent;
  assistant?: VoiceEvent;
  thinkingText?: string;
  confirmationStatus?: string;
  errorCode?: string;
  errorMessage?: string;
  retryable?: boolean;
  sampleRateHz?: number | null;
};

/**
 * Converts native event metadata into a bounded, typed event. Payload bodies
 * are intentionally bounded before they are accepted by the UI layer.
 */
export function normalizeVoiceGatewayEvent(
  input: unknown,
): NormalizedVoiceEvent | null {
  let value: unknown = input;
  if (typeof input === 'string') {
    if (input.length > MAX_EVENT_BYTES) {
      return null;
    }
    try {
      value = JSON.parse(input);
    } catch {
      return null;
    }
  }

  if (!value || typeof value !== 'object' || Array.isArray(value)) {
    return null;
  }

  try {
    if (JSON.stringify(value).length > MAX_EVENT_BYTES) {
      return null;
    }
  } catch {
    return null;
  }

  const record = value as Record<string, unknown>;
  const rawType = readString(record.type ?? record.event, 96);
  if (!rawType || !KNOWN_EVENT_TYPES.has(rawType)) {
    return null;
  }

  const timestampMs = readTimestamp(record.timestampMs ?? record.timestamp_ms);
  const transcript = readTranscriptEvent(rawType, record);
  const assistant = readAssistantEvent(rawType, record);
  const thinkingText =
    rawType === 'assistant.thinking'
      ? readString(record.text, MAX_TRANSCRIPT_LENGTH)
      : null;
  const errorCode = readString(record.code ?? record.errorCode, MAX_ID_LENGTH);
  const errorMessage = readString(record.message ?? record.errorMessage, 180);
  const retryable = readBoolean(record.retryable);
  const confirmationStatus = readString(record.status, 32);
  const sampleRateHz = readNumber(record.sampleRateHz ?? record.sample_rate_hz);
  return {
    type: rawType as VoiceServerEventType,
    eventId: readString(record.eventId ?? record.event_id, MAX_ID_LENGTH),
    sessionId: readString(record.sessionId ?? record.session_id, MAX_ID_LENGTH),
    turnId: readString(record.turnId ?? record.turn_id, MAX_ID_LENGTH),
    responseId: readString(
      record.responseId ?? record.response_id,
      MAX_ID_LENGTH,
    ),
    timestampMs,
    ...(transcript ? { transcript } : {}),
    ...(assistant ? { assistant } : {}),
    ...(thinkingText ? { thinkingText } : {}),
    ...(confirmationStatus ? { confirmationStatus } : {}),
    ...(errorCode ? { errorCode } : {}),
    ...(errorMessage ? { errorMessage } : {}),
    ...(retryable !== null ? { retryable } : {}),
    ...(sampleRateHz !== null ? { sampleRateHz } : {}),
  };
}

export class VoiceSocket {
  private readonly adapter: VoiceSocketAdapter;
  private readonly url: string;
  private readonly prepareConnection?: () => Promise<void>;
  private readonly appState: VoiceAppStateSource;
  private readonly now: () => number;
  private readonly connectTimeoutMs: number;
  private readonly heartbeatTimeoutMs: number;
  private readonly heartbeatCheckIntervalMs: number;
  private readonly reconnectDelaysMs: number[];
  private readonly continuousListening: boolean;
  private readonly autoStartSession: boolean;
  private readonly listeners = new Set<VoiceSocketListener>();
  private readonly seenEventIds = new Set<string>();
  private readonly seenFallbackEvents = new Set<string>();
  private readonly retiredSessionIds = new Set<string>();
  private readonly retiredTurnIds = new Set<string>();
  private readonly retiredResponseIds = new Set<string>();
  private conversationState: ConversationState = createConversationState();
  private pendingConversationEvents: VoiceEvent[] = [];
  private conversationFlushTimer: ReturnType<typeof setTimeout> | null = null;
  private snapshot: VoiceSocketSnapshot = { ...INITIAL_SNAPSHOT };
  private started = false;
  private explicitStop = false;
  private desiredConnection = false;
  private desiredSession = false;
  private hadConnected = false;
  private allowAutoReconnect = false;
  private sessionStartInFlight = false;
  private turnStartedAtMs: number | null = null;
  private connectPromise: Promise<void> | null = null;
  private reconnectTimer: ReturnType<typeof setTimeout> | null = null;
  private autoListenTimer: ReturnType<typeof setTimeout> | null = null;
  private speechEndCommitTimer: ReturnType<typeof setTimeout> | null = null;
  private heartbeatTimer: ReturnType<typeof setInterval> | null = null;
  private statusUnsubscribe: (() => void) | null = null;
  private eventUnsubscribe: (() => void) | null = null;
  private vadUnsubscribe: (() => void) | null = null;
  private appStateSubscription: { remove: () => void } | null = null;
  private speechEndedAtMs: number | null = null;
  private responseServerCompleted = false;
  private ttsPlaybackTerminal = true;
  private bargeInInFlight = false;
  private autoCommitBargeInTurn = false;
  private bargeInSpeechEndedPending = false;
  private bargeInCommitInFlight = false;
  private bargeInTurnId: string | null = null;
  private ttsPlaybackStartedAtMs: number | null = null;
  private ttsPlaybackResponseId: string | null = null;
  private confirmationAwaitingVoice = false;
  private confirmationPromptPlaybackCompleted = false;
  private confirmationTurnStartInFlight = false;
  private sileroSpeechSegmentStartedAtMs: number | null = null;
  private sileroSpeechSegmentStartedDuringGuard = false;
  private autoListenSuppressed = false;

  constructor(options: VoiceSocketOptions = {}) {
    this.adapter = options.adapter ?? nativeVoiceSocketAdapter;
    this.url = options.url ?? VOICE_GATEWAY_URL;
    this.prepareConnection = options.prepareConnection;
    this.appState = (options.appState ?? AppState) as VoiceAppStateSource;
    this.now = options.now ?? (() => Date.now());
    this.connectTimeoutMs =
      options.connectTimeoutMs ?? DEFAULT_CONNECT_TIMEOUT_MS;
    this.heartbeatTimeoutMs =
      options.heartbeatTimeoutMs ?? DEFAULT_HEARTBEAT_TIMEOUT_MS;
    this.heartbeatCheckIntervalMs =
      options.heartbeatCheckIntervalMs ?? DEFAULT_HEARTBEAT_CHECK_INTERVAL_MS;
    this.reconnectDelaysMs =
      options.reconnectDelaysMs ?? DEFAULT_RECONNECT_DELAYS_MS;
    this.continuousListening = options.continuousListening ?? true;
    this.autoStartSession = options.autoStartSession ?? true;
    this.snapshot = {
      ...INITIAL_SNAPSHOT,
      continuousListening: this.continuousListening,
    };
  }

  start(): void {
    if (this.started) {
      return;
    }

    this.started = true;
    this.statusUnsubscribe = this.adapter.subscribeStatus(status =>
      this.handleStatus(status),
    );
    this.eventUnsubscribe = this.adapter.subscribeEvent(event =>
      this.handleEvent(event),
    );
    this.vadUnsubscribe =
      this.adapter.subscribeVad?.(event => this.handleVadEvent(event)) ?? null;
    this.appStateSubscription = this.appState.addEventListener(
      'change',
      nextState => {
        if (nextState === 'active') {
          this.reconcile().catch(() => undefined);
        }
      },
    );
    this.heartbeatTimer = setInterval(
      () => this.checkHeartbeat(),
      this.heartbeatCheckIntervalMs,
    );
    if (this.desiredConnection) {
      this.reconcile().catch(() => undefined);
    }
  }

  isStarted(): boolean {
    return this.started;
  }

  subscribe(listener: VoiceSocketListener): () => void {
    this.listeners.add(listener);
    listener(this.snapshot);
    return () => this.listeners.delete(listener);
  }

  getSnapshot(): VoiceSocketSnapshot {
    return this.snapshot;
  }

  async connect(): Promise<void> {
    this.start();
    this.explicitStop = false;
    this.desiredConnection = true;
    if (this.autoStartSession) {
      this.desiredSession = true;
    }
    this.allowAutoReconnect = false;
    this.clearReconnectTimer();

    if (this.snapshot.connection === 'connected') {
      return;
    }
    if (this.connectPromise) {
      return this.connectPromise;
    }

    const connectionPromise = this.reconcileAndConnect();
    this.connectPromise = connectionPromise;
    try {
      return await connectionPromise;
    } finally {
      if (this.connectPromise === connectionPromise) {
        this.connectPromise = null;
      }
    }
  }

  private async reconcileAndConnect(): Promise<void> {
    // Reconcile the native transport before opening a new socket. The native
    // WebSocket can outlive a stale JS snapshot after a session/heartbeat
    // transition; calling connect in that state creates an avoidable
    // "already connected" failure.
    try {
      const nativeStatus = await this.adapter.getStatus();
      this.handleStatus(nativeStatus);
      // A stale CLOSING/DISCONNECTED status can make handleStatus schedule a
      // bounded automatic retry. A deliberate connect/retry owns this attempt,
      // so do not leave that second transport open behind it.
      this.clearReconnectTimer();
      if (isTransportConnected(nativeStatus)) {
        return;
      }
    } catch {
      // A failed status read should not prevent the normal connect attempt.
    }

    this.setSnapshot({ connection: 'connecting', error: null });
    await this.openTransport(false);
  }

  async retry(): Promise<void> {
    this.clearReconnectTimer();
    this.snapshot = {
      ...this.snapshot,
      connection: 'connecting',
      reconnectAttempt: 0,
      error: null,
    };
    this.notify();
    return this.connect();
  }

  async startSession(): Promise<void> {
    this.start();
    this.explicitStop = false;
    this.desiredConnection = true;
    this.desiredSession = true;
    this.allowAutoReconnect = false;

    if (this.snapshot.connection !== 'connected') {
      await this.connect();
    }
    if (this.snapshot.session === 'ready' || this.sessionStartInFlight) {
      return;
    }

    this.setSnapshot({ session: 'starting', error: null });
    await this.requestSessionStart(null);
  }

  private waitForReadySession(): Promise<void> {
    if (
      this.snapshot.connection === 'connected' &&
      this.snapshot.session === 'ready' &&
      this.snapshot.heartbeat === 'healthy' &&
      !this.reconnectTimer
    ) {
      return Promise.resolve();
    }

    if (
      this.snapshot.connection !== 'connected' ||
      this.snapshot.session !== 'starting'
    ) {
      return Promise.reject(new Error('Start a voice session before starting a turn.'));
    }

    return new Promise((resolve, reject) => {
      let settled = false;
      let unsubscribe: (() => void) | null = null;
      const finish = (error?: Error) => {
        if (settled) {
          return;
        }
        settled = true;
        clearTimeout(timeout);
        unsubscribe?.();
        if (error) {
          reject(error);
        } else {
          resolve();
        }
      };
      const timeout = setTimeout(() => {
        finish(new Error('Voice session startup timed out. Try again.'));
      }, this.connectTimeoutMs);

      unsubscribe = this.subscribe(snapshot => {
        if (
          snapshot.connection === 'connected' &&
          snapshot.session === 'ready' &&
          snapshot.heartbeat === 'healthy' &&
          !this.reconnectTimer
        ) {
          finish();
        } else if (
          ['failed', 'degraded', 'reconnecting', 'disconnected'].includes(
            snapshot.connection,
          ) ||
          snapshot.session === 'idle'
        ) {
          finish(
            new Error(
              snapshot.error ?? 'Voice session failed to start. Try again.',
            ),
          );
        }
      });
    });
  }

  async startTurn(options: VoiceTurnStartOptions = {}): Promise<void> {
    if (this.snapshot.session === 'starting') {
      await this.waitForReadySession();
    }
    if (
      this.snapshot.session !== 'ready' ||
      this.snapshot.connection !== 'connected' ||
      this.snapshot.heartbeat !== 'healthy' ||
      this.reconnectTimer
    ) {
      throw new Error('Start a voice session before starting a turn.');
    }
    if (this.snapshot.ttsPlaybackState === 'speaking') {
      throw new Error(
        'Wait for playback-aware speech confirmation before starting a turn.',
      );
    }
    this.clearAutoListenTimer();
    this.clearSpeechEndCommitTimer();
    const queueFollowUp =
      ['committing', 'waiting'].includes(this.snapshot.turn) &&
      !this.confirmationAwaitingVoice;
    if (queueFollowUp) {
      options = {
        ...options,
        preserveMicrophone: true,
        autoCommitOnSpeechEnd: true,
      };
      this.autoListenSuppressed = false;
      this.setSnapshot({ followUpQueued: true, error: null });
    } else if (this.snapshot.turn !== 'idle') {
      throw new Error('Finish the current voice turn before starting another.');
    } else {
      this.autoListenSuppressed = false;
    }
    if (['failed', 'cancelled', 'completed'].includes(this.snapshot.turn)) {
      this.retireCurrentTurnCorrelation();
      this.setSnapshot({
        turn: 'idle',
        turnId: null,
        responseId: null,
        ttsPlaybackState: 'idle',
        ttsResponseId: null,
        ttsError: null,
        waitPhrase: null,
        followUpQueued: false,
      });
    }

    try {
      const permission = await this.adapter.requestMicrophonePermission?.();
      if (
        permission &&
        !['granted', 'authorized', 'limited'].includes(permission.toLowerCase())
      ) {
        throw new Error('Microphone permission is required for a voice turn.');
      }
      if (!options.preserveMicrophone) {
        await this.stopMicrophoneSafely();
        await this.adapter.startMicrophone?.();
      }
      this.responseServerCompleted = false;
      if (!queueFollowUp) {
        this.ttsPlaybackTerminal = true;
        this.ttsPlaybackStartedAtMs = null;
        this.ttsPlaybackResponseId = null;
      }
      this.sileroSpeechSegmentStartedAtMs = null;
      this.sileroSpeechSegmentStartedDuringGuard = false;
      this.autoCommitBargeInTurn = Boolean(options.autoCommitOnSpeechEnd);
      this.bargeInSpeechEndedPending = false;
      this.bargeInCommitInFlight = false;
      if (!options.autoCommitOnSpeechEnd) {
        this.bargeInTurnId = null;
      }
      this.turnStartedAtMs = this.now();
      this.speechEndedAtMs = null;
      emitLatencyTrace({
        sessionId: this.snapshot.sessionId,
        turnId: null,
        responseId: null,
        component: 'client',
        event: 'turn_start_requested',
        metadata: { include_pre_roll: Boolean(options.includePreRoll) },
      });
      this.setSnapshot({
        turn: 'starting',
        speechDetected: false,
        ttsPlaybackState: 'idle',
        ttsResponseId: null,
        ttsError: null,
        followUpQueued: queueFollowUp,
        error: null,
      });
      this.handleStatus(
        await this.adapter.startTurn(null, Boolean(options.includePreRoll)),
      );
      this.ensureTranscriptPlaceholder('listening');
    } catch (error) {
      this.turnStartedAtMs = null;
      this.autoCommitBargeInTurn = false;
      this.bargeInSpeechEndedPending = false;
      this.setSnapshot({ followUpQueued: false });
      this.markCurrentTranscriptError(error);
      this.setSnapshot({
        turn: 'idle',
        speechDetected: false,
        error: safeVoiceError(error),
      });
      throw new Error(this.snapshot.error ?? 'Unable to start the voice turn.');
    }
  }

  async commitTurn(): Promise<void> {
    if (
      !['starting', 'recording', 'speech_detected'].includes(this.snapshot.turn)
    ) {
      throw new Error('There is no active voice turn to finish.');
    }
    this.clearSpeechEndCommitTimer();

    const durationMs = Math.max(
      0,
      Math.round(this.now() - (this.turnStartedAtMs ?? this.now())),
    );
    this.speechEndedAtMs = this.speechEndedAtMs ?? this.now();
    emitLatencyTrace({
      sessionId: this.snapshot.sessionId,
      turnId: this.snapshot.turnId,
      responseId: this.snapshot.responseId,
      component: 'client',
      event: 'turn_commit_sent',
      metadata: { duration_ms: durationMs },
    });
    this.markCurrentTranscriptPhase('transcribing');
    this.setSnapshot({
      turn: 'committing',
      speechDetected: false,
      error: null,
    });
    try {
      const status = await this.adapter.commitAudio(durationMs);
      // Keep AudioRecord alive after commit. Native AEC/NS and Silero VAD
      // continue processing speaker-aware microphone frames while the
      // assistant response is buffered or playing. The transport does not
      // upload frames once commitAudio marks the turn inactive.
      this.handleStatus(status);
    } catch (error) {
      this.markCurrentTranscriptError(error);
      this.setSnapshot({ turn: 'failed', error: safeVoiceError(error) });
      throw new Error(
        this.snapshot.error ?? 'Unable to finish the voice turn.',
      );
    }
  }

  async cancelTurn(reason = 'client_requested'): Promise<void> {
    if (!this.snapshot.responseId && this.snapshot.turn === 'idle') {
      return;
    }
    this.clearAutoListenTimer();
    this.clearSpeechEndCommitTimer();
    this.autoListenSuppressed = true;
    const turnId = this.snapshot.turnId;
    const responseId = this.snapshot.responseId;
    this.markCurrentTranscriptCancelled();
    if (turnId && responseId) {
      this.applyConversationEvent({
        type: 'assistant.response.cancelled',
        sessionId: this.snapshot.sessionId,
        turnId,
        responseId,
        timestampMs: this.now(),
      });
      // Reject late deltas as soon as the user presses Stop, before the
      // network cancellation round trip completes.
      this.retireCorrelation(null, null, responseId);
    }
    try {
      this.setSnapshot({
        turn: 'cancelled',
        speechDetected: false,
        error: null,
      });
      await this.stopMicrophoneSafely();
      const abortAll =
        reason === 'barge_in' ? undefined : this.adapter.abortAll;
      const status = abortAll
        ? await abortAll.call(this.adapter, reason)
        : await this.adapter.cancelResponse(reason);
      this.handleStatus(status);
    } catch (error) {
      this.markCurrentTranscriptError(error);
      this.setSnapshot({ turn: 'failed', error: safeVoiceError(error) });
      throw new Error(
        this.snapshot.error ?? 'Unable to cancel the voice turn.',
      );
    }
    if (this.snapshot.turn === 'cancelled' && this.snapshot.turnId === turnId) {
      this.retireCurrentTurnCorrelation();
      this.turnStartedAtMs = null;
      this.setSnapshot({
        turn: 'idle',
        turnId: null,
        responseId: null,
        session: 'ready',
        waitPhrase: null,
        followUpQueued: false,
      });
    }
  }

  /** Stops only native playback. The text response and its durable state stay visible. */
  async stopPlayback(): Promise<void> {
    if (
      !this.snapshot.ttsResponseId ||
      !['buffering', 'speaking'].includes(this.snapshot.ttsPlaybackState)
    ) {
      return;
    }
    this.setSnapshot({ ttsPlaybackState: 'stopping', ttsError: null });
    try {
      await this.adapter.stopPlayback?.();
      this.ttsPlaybackTerminal = true;
      this.setSnapshot({ ttsPlaybackState: 'idle' });
      this.finalizeResponseIfReady();
    } catch {
      this.setSnapshot({
        ttsPlaybackState: 'failed',
        ttsError: 'Voice output could not be stopped.',
      });
    }
  }

  async retryResponse(turnId: string): Promise<void> {
    const userMessage = this.conversationState.messages.find(
      message =>
        message.role === 'user' &&
        message.turnId === turnId &&
        message.final &&
        message.text.trim(),
    );
    const failedResponse = this.conversationState.messages.find(
      message =>
        message.role === 'assistant' &&
        message.turnId === turnId &&
        message.status === 'failed',
    );
    if (
      !userMessage ||
      userMessage.role !== 'user' ||
      !failedResponse ||
      failedResponse.role !== 'assistant'
    ) {
      throw new Error('This response cannot be retried.');
    }
    if (!failedResponse.retryable) {
      throw new Error(mapConversationError(failedResponse.errorCode).message);
    }
    if (!this.adapter.retryResponse) {
      throw new Error('Retry is not available for this voice session.');
    }

    this.retireCorrelation(null, null, failedResponse.responseId);
    this.setSnapshot({
      turn: 'waiting',
      responseId: null,
      error: null,
      transcriptError: null,
    });
    try {
      const status = await this.adapter.retryResponse(
        turnId,
        failedResponse.responseId,
        userMessage.text,
      );
      if (!status.connected) {
        this.handleStatus(status);
      }
    } catch (error) {
      const message = safeVoiceError(error);
      this.setSnapshot({ turn: 'failed', error: message });
      throw new Error(message);
    }
  }

  async resolveConfirmation(
    confirmationId: string,
    toolCallId: string,
    decision: 'approve' | 'deny',
  ): Promise<void> {
    if (!confirmationId || !toolCallId) {
      throw new Error('This confirmation is no longer available.');
    }
    if (!this.adapter.resolveConfirmation) {
      throw new Error(
        'Confirmation controls are unavailable for this session.',
      );
    }
    try {
      this.handleStatus(
        await this.adapter.resolveConfirmation(
          confirmationId,
          toolCallId,
          decision,
        ),
      );
    } catch (error) {
      const message = safeVoiceError(error);
      this.setSnapshot({ error: message });
      throw new Error(message);
    }
  }

  async resetConversation(): Promise<void> {
    this.clearAutoListenTimer();
    this.autoListenSuppressed = false;
    if (this.snapshot.sessionId && this.adapter.resetConversation) {
      this.clearConversationState();
      this.setSnapshot({
        transcriptMessages: [],
        transcriptError: null,
        firstTextAtMs: null,
        waitPhrase: null,
        followUpQueued: false,
        error: null,
      });
      try {
        this.handleStatus(await this.adapter.resetConversation());
      } catch (error) {
        const message = safeVoiceError(error);
        this.setSnapshot({ error: message });
        throw new Error(message);
      }
      return;
    }
    if (this.snapshot.responseId) {
      await this.cancelTurn('new_conversation');
    } else if (this.snapshot.turn !== 'idle') {
      await this.adapter.stopMicrophone?.();
      this.retireCurrentTurnCorrelation();
      this.setSnapshot({
        turn: 'idle',
        turnId: null,
        responseId: null,
        speechDetected: false,
      });
    }
    this.flushConversationEvents();
    this.conversationState = clearConversation();
    this.setSnapshot({
      conversationMessages: [],
      transcriptMessages: [],
      transcriptError: null,
      firstTextAtMs: null,
      conversationRenderCompletedAtMs: null,
      waitPhrase: null,
      followUpQueued: false,
    });
  }

  markConversationRendered(responseId: string): void {
    const next = markConversationRenderedState(
      this.conversationState,
      responseId,
      this.now(),
    );
    if (next === this.conversationState) {
      return;
    }
    this.conversationState = next;
    this.setSnapshot({
      conversationMessages: next.messages,
      conversationRenderCompletedAtMs: this.now(),
    });
  }

  async endSession(reason = 'client_requested'): Promise<void> {
    if (this.snapshot.session === 'idle' && !this.snapshot.sessionId) {
      return;
    }
    this.desiredSession = false;
    this.desiredConnection = false;
    this.explicitStop = true;
    this.allowAutoReconnect = false;
    this.clearAutoListenTimer();
    this.clearSpeechEndCommitTimer();
    this.clearReconnectTimer();
    this.setSnapshot({ session: 'ending', error: null });
    try {
      this.handleStatus(await this.adapter.endSession(reason));
    } catch (error) {
      this.setSnapshot({ connection: 'failed', error: safeVoiceError(error) });
      throw new Error(
        this.snapshot.error ?? 'Unable to end the voice session.',
      );
    }
  }

  async disconnect(): Promise<void> {
    this.desiredConnection = false;
    this.desiredSession = false;
    this.explicitStop = true;
    this.allowAutoReconnect = false;
    this.clearAutoListenTimer();
    this.clearSpeechEndCommitTimer();
    this.clearReconnectTimer();
    try {
      this.handleStatus(await this.adapter.disconnect());
    } finally {
      this.resetToDisconnected();
    }
  }

  async stop(reason = 'client_stopped'): Promise<void> {
    this.desiredConnection = false;
    this.desiredSession = false;
    this.explicitStop = true;
    this.allowAutoReconnect = false;
    this.clearAutoListenTimer();
    this.clearSpeechEndCommitTimer();
    this.clearReconnectTimer();

    if (!this.started) {
      this.resetToDisconnected();
      return;
    }

    const operations: Promise<unknown>[] = [];
    if (this.snapshot.responseId) {
      operations.push(this.adapter.cancelResponse(reason));
    }
    if (this.snapshot.sessionId || this.snapshot.session !== 'idle') {
      operations.push(this.adapter.endSession(reason));
    }
    if (this.snapshot.connection !== 'disconnected') {
      operations.push(this.adapter.disconnect());
    }
    if (this.adapter.stopMicrophone) {
      operations.push(this.adapter.stopMicrophone());
    }
    await Promise.allSettled(operations);
    this.resetToDisconnected();
  }

  async dispose(): Promise<void> {
    await this.stop('dispose');
    this.statusUnsubscribe?.();
    this.eventUnsubscribe?.();
    this.appStateSubscription?.remove();
    this.statusUnsubscribe = null;
    this.eventUnsubscribe = null;
    this.vadUnsubscribe?.();
    this.vadUnsubscribe = null;
    this.appStateSubscription = null;
    this.started = false;
    if (this.heartbeatTimer) {
      clearInterval(this.heartbeatTimer);
      this.heartbeatTimer = null;
    }
  }

  private async reconcile(): Promise<void> {
    if (!this.started || this.appState.currentState !== 'active') {
      return;
    }

    try {
      const status = await this.adapter.getStatus();
      this.handleStatus(status);
      if (
        this.desiredConnection &&
        !status.connected &&
        !this.explicitStop &&
        !this.reconnectTimer
      ) {
        if (this.hadConnected || this.desiredSession) {
          this.scheduleReconnect('lifecycle');
        }
      }
      if (status.connected && this.desiredSession && !status.sessionStarted) {
        this.requestSessionStart(this.snapshot.sessionId).catch(
          () => undefined,
        );
      }
    } catch (error) {
      if (this.desiredConnection && !this.explicitStop) {
        this.setSnapshot({
          connection: 'degraded',
          error: safeVoiceError(error),
        });
      }
    }
  }

  private async stopMicrophoneSafely(): Promise<void> {
    try {
      await this.adapter.stopMicrophone?.();
    } catch {
      // Capture may already have stopped after VAD/transport cancellation.
    }
  }

  private async openTransport(isReconnect: boolean): Promise<void> {
    try {
      // The native transport reads its bearer token from secure storage. Keep
      // that token current before every new socket so reconnects cannot loop
      // on an expired access token after the HTTP session has been refreshed.
      await this.prepareConnection?.();
      const status = await this.adapter.connect(this.url);
      this.handleStatus(status);
      await this.waitForConnected();
      if (this.desiredSession && !this.sessionStartInFlight) {
        await this.requestSessionStart(this.snapshot.sessionId);
      }
    } catch (error) {
      const message = safeVoiceError(error);
      if (isReconnect) {
        await this.closeTransportForReconnect();
        this.scheduleReconnect('connect');
      } else {
        this.desiredConnection = false;
        this.explicitStop = true;
        this.setSnapshot({ connection: 'failed', error: message });
      }
      throw new Error(message);
    }
  }

  private waitForConnected(): Promise<void> {
    if (this.snapshot.connection === 'connected') {
      return Promise.resolve();
    }

    return new Promise((resolve, reject) => {
      let settled = false;
      let unsubscribe: () => void = () => undefined;
      const timeout = setTimeout(() => {
        if (settled) {
          return;
        }
        settled = true;
        unsubscribe();
        reject(new Error('Voice connection timed out. Try again.'));
      }, this.connectTimeoutMs);
      unsubscribe = this.subscribe(snapshot => {
        if (settled) {
          return;
        }
        if (snapshot.connection === 'connected') {
          settled = true;
          clearTimeout(timeout);
          unsubscribe();
          resolve();
        } else if (snapshot.connection === 'failed') {
          settled = true;
          clearTimeout(timeout);
          unsubscribe();
          reject(new Error(snapshot.error ?? 'Voice connection failed.'));
        }
      });
    });
  }

  private async requestSessionStart(
    resumeSessionId: string | null,
  ): Promise<void> {
    if (
      this.sessionStartInFlight ||
      !this.desiredSession ||
      this.snapshot.connection !== 'connected'
    ) {
      return;
    }

    this.sessionStartInFlight = true;
    try {
      this.handleStatus(await this.adapter.startSession(resumeSessionId));
    } catch (error) {
      this.sessionStartInFlight = false;
      this.setSnapshot({
        session: 'idle',
        error: safeVoiceError(error),
      });
      throw new Error(
        this.snapshot.error ?? 'Unable to start the voice session.',
      );
    }
  }

  private handleStatus(status: VoiceGatewayStatus): void {
    const nativeState = status.state.toUpperCase();
    const connected =
      status.connected ||
      [
        'CONNECTED',
        'SESSION_STARTING',
        'SESSION_READY',
        'TURN_STARTING',
        'STREAMING_AUDIO',
      ].includes(nativeState);

    if (connected) {
      const connectionBecameConnected =
        this.snapshot.connection !== 'connected';
      const sessionIsStable =
        status.sessionStarted && nativeState === 'SESSION_READY';
      this.hadConnected = true;
      if (status.sessionStarted) {
        this.sessionStartInFlight = false;
      }
      this.setSnapshot({
        connection: 'connected',
        heartbeat: this.snapshot.heartbeat === 'missed' ? 'unknown' : 'healthy',
        error: null,
        ...(connectionBecameConnected ? { lastHeartbeatAtMs: this.now() } : {}),
        ...(sessionIsStable ? { reconnectAttempt: 0 } : {}),
        ...(status.sessionStarted
          ? { session: 'ready' as VoiceSessionState }
          : {}),
        ...(status.sessionId ? { sessionId: status.sessionId } : {}),
        ...(status.turnId ? { turnId: status.turnId } : {}),
        ...(status.responseId ? { responseId: status.responseId } : {}),
      });
      if (this.desiredSession && !status.sessionStarted) {
        this.setSnapshot({ session: 'starting', error: null });
        this.requestSessionStart(this.snapshot.sessionId).catch(
          () => undefined,
        );
      }
      if (sessionIsStable) {
        this.scheduleContinuousListen();
      }
      return;
    }

    if (nativeState === 'ERROR') {
      if (isAuthenticationExpiredError(status.lastError)) {
        this.handleAuthenticationExpired();
        return;
      }
      this.setSnapshot({
        connection: 'failed',
        error: safeVoiceError(status.lastError),
        heartbeat: 'missed',
      });
      this.suppressAudioForTransportFailure('native_transport_error');
      if (
        this.allowAutoReconnect &&
        this.desiredConnection &&
        !this.explicitStop
      ) {
        this.scheduleReconnect('transport-error');
      }
      return;
    }

    if (nativeState === 'DISCONNECTED' || nativeState === 'CLOSING') {
      if (this.explicitStop || !this.desiredConnection) {
        this.resetToDisconnected();
        return;
      }
      if (this.hadConnected || this.allowAutoReconnect) {
        this.suppressAudioForTransportFailure('transport_closed');
        this.scheduleReconnect('transport-close');
        return;
      }
      this.setSnapshot({
        connection: 'failed',
        error: 'Voice connection closed.',
      });
    }
  }

  private handleEvent(input: unknown): void {
    if (this.explicitStop) {
      return;
    }
    const event = normalizeVoiceGatewayEvent(input);
    if (!event) {
      this.recordDroppedEvent(true);
      return;
    }

    const duplicateKey = event.eventId
      ? `id:${event.eventId}`
      : `fallback:${event.type}:${event.sessionId ?? ''}:${
          event.turnId ?? ''
        }:${event.responseId ?? ''}:${event.timestampMs ?? ''}`;
    const seen = event.eventId
      ? this.seenEventIds.has(duplicateKey)
      : this.seenFallbackEvents.has(duplicateKey);
    if (seen) {
      this.recordDroppedEvent(false);
      return;
    }
    this.rememberEvent(duplicateKey, Boolean(event.eventId));

    const usesClientClock = CLIENT_CLOCK_EVENT_TYPES.has(event.type);
    if (
      !usesClientClock &&
      event.timestampMs !== null &&
      this.snapshot.lastEventAtMs !== null &&
      event.timestampMs < this.snapshot.lastEventAtMs
    ) {
      this.recordDroppedEvent(false);
      return;
    }
    if (this.isStaleEvent(event)) {
      this.recordDroppedEvent(false);
      return;
    }
    if (event.transcript && !this.canAcceptTranscript(event.transcript)) {
      this.recordDroppedEvent(false);
      return;
    }

    const receivedAtMs = this.now();
    const clientTraceEvent =
      event.type === 'server.turn.ready'
        ? 'turn_ready_received'
        : event.type === 'assistant.response.started'
        ? 'response_received'
        : event.type === 'transcript.final'
        ? 'client_stt_final'
        : event.type === 'tts.completed'
        ? 'tts_generation_complete_received'
        : event.type;
    emitLatencyTrace({
      sessionId: event.sessionId,
      turnId: event.turnId,
      responseId: event.responseId,
      component: 'client',
      event: clientTraceEvent,
      metadata: {
        gateway_timestamp_ms: event.timestampMs,
        received_at_ms: receivedAtMs,
      },
    });
    this.setSnapshot({
      eventSequence: this.snapshot.eventSequence + 1,
      lastEvent: event.type,
      lastEventAtMs: usesClientClock
        ? this.snapshot.lastEventAtMs
        : event.timestampMs ?? receivedAtMs,
      ...(event.sessionId ? { sessionId: event.sessionId } : {}),
      ...(event.turnId ? { turnId: event.turnId } : {}),
      ...(event.responseId ? { responseId: event.responseId } : {}),
    });

    if (event.transcript) {
      this.handleTranscriptEvent(event.transcript);
    }
    if (event.assistant) {
      this.applyConversationEvent(event.assistant);
      if (
        event.assistant.type === 'tool.status' &&
        event.assistant.confirmationId &&
        event.assistant.status !== 'confirmation_required'
      ) {
        this.confirmationAwaitingVoice = false;
        this.confirmationPromptPlaybackCompleted = false;
        this.setSnapshot({ confirmationAwaitingVoice: false });
      }
    }

    if (event.type === 'server.pong') {
      this.setSnapshot({
        connection: 'connected',
        heartbeat: 'healthy',
        lastHeartbeatAtMs: receivedAtMs,
        reconnectAttempt: 0,
        error: null,
      });
      return;
    }

    switch (event.type) {
      case 'voice.connection.opened':
        this.hadConnected = true;
        this.setSnapshot({ connection: 'connected', error: null });
        break;
      case 'server.session.ready':
        this.sessionStartInFlight = false;
        this.autoListenSuppressed = false;
        console.info('TRANSPORT_HEALTHY_AUDIO_RESUMED', {
          sessionId: event.sessionId,
          timestampMs: this.now(),
        });
        emitLatencyTrace({
          sessionId: event.sessionId,
          turnId: event.turnId,
          responseId: event.responseId,
          component: 'client',
          event: 'transport_healthy_audio_resumed',
        });
        this.setSnapshot({
          connection: 'connected',
          session: 'ready',
          heartbeat: 'healthy',
          lastHeartbeatAtMs: receivedAtMs,
          reconnectAttempt: 0,
          error: null,
        });
        this.scheduleContinuousListen();
        break;
      case 'server.conversation.reset':
        this.clearConversationState();
        this.retireCorrelation(
          this.snapshot.sessionId,
          this.snapshot.turnId,
          this.snapshot.responseId,
        );
        this.responseServerCompleted = false;
        this.ttsPlaybackTerminal = true;
        this.confirmationAwaitingVoice = false;
        this.confirmationPromptPlaybackCompleted = false;
        this.confirmationTurnStartInFlight = false;
        this.setSnapshot({
          turn: 'idle',
          sessionId: null,
          turnId: null,
          responseId: null,
          ttsPlaybackState: 'idle',
          ttsResponseId: null,
          ttsError: null,
          waitPhrase: null,
          followUpQueued: false,
          transcriptMessages: [],
          transcriptError: null,
          session: 'ready',
        });
        break;
      case 'server.session.ending':
        this.setSnapshot({ session: 'ending' });
        break;
      case 'voice.session.stale.reaped':
        this.clearConversationState();
        this.retireCorrelation(
          event.sessionId ?? this.snapshot.sessionId,
          event.turnId ?? this.snapshot.turnId,
          event.responseId ?? this.snapshot.responseId,
        );
        this.sessionStartInFlight = false;
        this.confirmationAwaitingVoice = false;
        this.confirmationPromptPlaybackCompleted = false;
        this.confirmationTurnStartInFlight = false;
        this.desiredSession = false;
        this.setSnapshot({
          session: 'idle',
          turn: 'idle',
          sessionId: null,
          turnId: null,
          responseId: null,
          ttsPlaybackState: 'idle',
          ttsResponseId: null,
          ttsError: null,
          confirmationAwaitingVoice: false,
          speechDetected: false,
          transcriptMessages: [],
          transcriptError: null,
          error: 'The previous voice session expired. Start a new session.',
        });
        break;
      case 'server.session.ended':
        this.clearConversationState();
        this.retireCorrelation(
          event.sessionId ?? this.snapshot.sessionId,
          event.turnId ?? this.snapshot.turnId,
          event.responseId ?? this.snapshot.responseId,
        );
        this.sessionStartInFlight = false;
        this.confirmationAwaitingVoice = false;
        this.confirmationPromptPlaybackCompleted = false;
        this.confirmationTurnStartInFlight = false;
        this.desiredSession = false;
        this.desiredConnection = false;
        this.explicitStop = true;
        this.setSnapshot({
          connection: 'disconnected',
          session: 'idle',
          turn: 'idle',
          sessionId: null,
          turnId: null,
          responseId: null,
          confirmationAwaitingVoice: false,
          speechDetected: false,
          transcriptMessages: [],
          transcriptError: null,
        });
        break;
      case 'voice.turn.started':
        this.setSnapshot({ turn: 'starting', speechDetected: false });
        this.ensureTranscriptPlaceholder('listening');
        break;
      case 'server.turn.ready':
        this.setSnapshot({
          connection: 'connected',
          turn: 'recording',
          speechDetected: false,
          followUpQueued: false,
          waitPhrase: null,
        });
        this.ensureTranscriptPlaceholder('listening');
        if (this.autoCommitBargeInTurn) {
          this.bargeInTurnId = event.turnId;
          console.info('BARGE_IN_NEW_TURN_CREATED', {
            turnId: event.turnId,
            responseId: event.responseId,
            timestampMs: this.now(),
          });
          if (this.bargeInSpeechEndedPending && !this.bargeInCommitInFlight) {
            this.bargeInSpeechEndedPending = false;
            this.scheduleSpeechEndCommit();
          }
        }
        break;
      case 'voice.audio.commit.received':
      case 'voice.turn.finalization.started':
      case 'voice.transcript.final.delivered':
        this.markCurrentTranscriptPhase('transcribing');
        this.setSnapshot({ turn: 'waiting', speechDetected: false });
        break;
      case 'assistant.response.started':
      case 'assistant.request.started':
      case 'assistant.text.final':
      case 'llm.response.completed':
        if (
          event.type === 'assistant.response.started' &&
          event.turnId === this.bargeInTurnId
        ) {
          console.info('NEW_RESPONSE_STARTED', {
            turnId: event.turnId,
            responseId: event.responseId,
            timestampMs: this.now(),
          });
        }
        this.setSnapshot({
          turn: 'waiting',
          speechDetected: false,
          waitPhrase: null,
        });
        break;
      case 'assistant.thinking':
        this.setSnapshot({
          turn: 'waiting',
          speechDetected: false,
          waitPhrase: event.thinkingText ?? null,
        });
        break;
      case 'assistant.text.delta':
        this.setSnapshot({
          turn: 'waiting',
          speechDetected: false,
          waitPhrase: null,
        });
        break;
      case 'tts.started':
        this.ttsPlaybackTerminal = false;
        if (this.ttsPlaybackResponseId !== event.responseId) {
          this.ttsPlaybackResponseId = event.responseId;
          this.ttsPlaybackStartedAtMs = null;
        }
        this.setSnapshot({
          turn: 'waiting',
          ttsPlaybackState: 'buffering',
          ttsResponseId: event.responseId,
          ttsError: null,
        });
        break;
      case 'tts.playback.started':
        emitLatencyTrace({
          sessionId: event.sessionId,
          turnId: event.turnId,
          responseId: event.responseId,
          component: 'android',
          event: 'tts_playback_start',
        });
        this.ttsPlaybackTerminal = false;
        // Playback may report this lifecycle event more than once while a
        // response is being drained. The first timestamp is the only valid
        // origin for the startup guard; later chunks/events must not extend it.
        if (
          this.ttsPlaybackResponseId !== event.responseId ||
          this.ttsPlaybackStartedAtMs === null
        ) {
          this.ttsPlaybackResponseId = event.responseId;
          this.ttsPlaybackStartedAtMs = event.timestampMs ?? this.now();
        }
        this.setSnapshot({
          ttsPlaybackState: 'speaking',
          ttsResponseId: event.responseId,
          ttsError: null,
        });
        break;
      case 'tts.playback.completed':
        emitLatencyTrace({
          sessionId: event.sessionId,
          turnId: event.turnId,
          responseId: event.responseId,
          component: 'android',
          event: 'tts_playback_complete',
        });
        this.ttsPlaybackTerminal = true;
        this.ttsPlaybackStartedAtMs = null;
        this.ttsPlaybackResponseId = null;
        if (this.confirmationAwaitingVoice) {
          this.confirmationPromptPlaybackCompleted = true;
        }
        this.setSnapshot({
          ttsPlaybackState: 'completed',
          ttsResponseId: event.responseId,
        });
        this.finalizeResponseIfReady();
        this.maybeStartVoiceConfirmationTurn();
        break;
      case 'tts.playback.stopped':
      case 'tts.cancelled':
        this.confirmationPromptPlaybackCompleted = false;
        this.ttsPlaybackTerminal = true;
        this.ttsPlaybackStartedAtMs = null;
        this.ttsPlaybackResponseId = null;
        this.setSnapshot({
          ttsPlaybackState: 'idle',
          ttsResponseId: event.responseId ?? this.snapshot.ttsResponseId,
          ttsError: null,
        });
        this.finalizeResponseIfReady();
        this.maybeStartVoiceConfirmationTurn();
        break;
      case 'tts.failed':
        this.confirmationPromptPlaybackCompleted = false;
        this.ttsPlaybackTerminal = true;
        this.ttsPlaybackStartedAtMs = null;
        this.ttsPlaybackResponseId = null;
        this.setSnapshot({
          ttsPlaybackState: 'failed',
          ttsResponseId: event.responseId,
          ttsError:
            event.errorMessage ?? event.errorCode ?? 'Voice output failed.',
        });
        this.finalizeResponseIfReady();
        break;
      case 'voice.confirmation.required':
      case 'confirmation.required':
        this.confirmationAwaitingVoice = true;
        this.confirmationPromptPlaybackCompleted = false;
        this.setSnapshot({ confirmationAwaitingVoice: true });
        this.maybeStartVoiceConfirmationTurn();
        break;
      case 'confirmation.resolved':
        if (
          event.confirmationStatus &&
          event.confirmationStatus.toUpperCase() !== 'PENDING'
        ) {
          this.confirmationAwaitingVoice = false;
          this.confirmationPromptPlaybackCompleted = false;
          this.setSnapshot({ confirmationAwaitingVoice: false });
        }
        break;
      case 'transcript.partial':
      case 'voice.transcript.partial':
        this.setSnapshot({ turn: 'waiting', speechDetected: false });
        break;
      case 'transcript.final':
        if (event.transcript?.text.trim()) {
          this.setSnapshot({ turn: 'waiting', speechDetected: false });
        }
        break;
      case 'server.turn.completed':
        this.applyConversationEvent({
          type: 'turn.completed',
          sessionId: event.sessionId,
          turnId: event.turnId,
          responseId: event.responseId,
          timestampMs: event.timestampMs,
        });
        this.responseServerCompleted = true;
        if (!this.snapshot.followUpQueued) {
          this.setSnapshot({
            turn: this.ttsPlaybackTerminal ? 'completed' : 'waiting',
            speechDetected: false,
            session: 'ready',
            waitPhrase: null,
          });
          this.finalizeResponseIfReady();
        }
        this.maybeStartVoiceConfirmationTurn();
        break;
      case 'response.cancelled':
        this.applyConversationEvent({
          type: 'assistant.response.cancelled',
          sessionId: event.sessionId,
          turnId: event.turnId,
          responseId: event.responseId,
          timestampMs: event.timestampMs,
        });
        this.retireCorrelation(
          null,
          event.turnId ?? this.snapshot.turnId,
          event.responseId ?? this.snapshot.responseId,
        );
        this.turnStartedAtMs = null;
        this.responseServerCompleted = true;
        this.ttsPlaybackTerminal = true;
        this.confirmationAwaitingVoice = false;
        this.confirmationPromptPlaybackCompleted = false;
        this.confirmationTurnStartInFlight = false;
        this.markCurrentTranscriptCancelled();
        this.setSnapshot({
          turn: 'cancelled',
          speechDetected: false,
          turnId: null,
          responseId: null,
          confirmationAwaitingVoice: false,
          session: 'ready',
          waitPhrase: null,
          followUpQueued: false,
        });
        this.setSnapshot({ turn: 'idle' });
        this.stopMicrophoneSafely().catch(() => undefined);
        break;
      case 'server.turn.failed':
      case 'assistant.response.failed':
      case 'llm.response.failed':
        this.turnStartedAtMs = null;
        this.markCurrentTranscriptError(event.errorCode);
        const turnError = mapTranscriptError(event.errorCode);
        this.setSnapshot({
          turn: 'failed',
          speechDetected: false,
          transcriptError: turnError,
          session: 'ready',
          error: turnError.message,
        });
        this.stopMicrophoneSafely().catch(() => undefined);
        break;
      case 'server.error':
        if (isAuthenticationExpiredError(event.errorCode)) {
          this.handleAuthenticationExpired();
          break;
        }
        if (isTerminalSessionError(event.errorCode)) {
          this.handleUnavailableSession();
          break;
        }
        const serverError = mapTranscriptError(event.errorCode);
        if (event.turnId || this.snapshot.turnId) {
          this.markCurrentTranscriptError(event.errorCode);
        }
        this.setSnapshot({
          connection: 'failed',
          heartbeat: 'missed',
          transcriptError: event.errorCode?.toLowerCase().startsWith('stt_')
            ? serverError
            : this.snapshot.transcriptError,
          error: event.errorCode?.toLowerCase().startsWith('stt_')
            ? serverError.message
            : 'The voice session reported an error. Try again.',
        });
        this.suppressAudioForTransportFailure(
          `server_error:${event.errorCode ?? 'unknown'}`,
        );
        if (this.allowAutoReconnect && this.desiredConnection) {
          this.scheduleReconnect('server-error');
        }
        break;
      default:
        break;
    }
  }

  private handleUnavailableSession(): void {
    this.retireCorrelation(
      this.snapshot.sessionId,
      this.snapshot.turnId,
      this.snapshot.responseId,
    );
    this.clearConversationState();
    this.sessionStartInFlight = false;
    this.desiredSession = false;
    this.turnStartedAtMs = null;
    this.speechEndedAtMs = null;
    this.responseServerCompleted = false;
    this.ttsPlaybackTerminal = true;
    this.bargeInInFlight = false;
    this.confirmationAwaitingVoice = false;
    this.confirmationPromptPlaybackCompleted = false;
    this.confirmationTurnStartInFlight = false;
    this.stopMicrophoneSafely().catch(() => undefined);

    const shouldReconnect = this.desiredConnection && !this.explicitStop;
    this.allowAutoReconnect = shouldReconnect;
    this.setSnapshot({
      connection: shouldReconnect ? 'reconnecting' : 'failed',
      session: 'idle',
      turn: 'idle',
      heartbeat: 'unknown',
      sessionId: null,
      turnId: null,
      responseId: null,
      confirmationAwaitingVoice: false,
      speechDetected: false,
      waitPhrase: null,
      followUpQueued: false,
      transcriptMessages: [],
      transcriptError: null,
      lastHeartbeatAtMs: null,
      error: shouldReconnect
        ? 'The previous voice session expired. Reconnecting safely…'
        : 'The previous voice session expired. Start a new session.',
    });

    if (shouldReconnect && !this.reconnectTimer) {
      this.scheduleReconnect('stale-session');
    }
  }

  /**
   * An authenticated WebSocket cannot refresh its JWT in place. Drop the
   * expired session correlation, let prepareConnection refresh HTTP auth, and
   * create a fresh voice session after reconnecting instead of resuming a
   * session the backend has already released.
   */
  private handleAuthenticationExpired(): void {
    if (this.snapshot.connection === 'reconnecting' && this.reconnectTimer) {
      return;
    }
    this.retireCorrelation(
      this.snapshot.sessionId,
      this.snapshot.turnId,
      this.snapshot.responseId,
    );
    this.clearConversationState();
    this.sessionStartInFlight = false;
    this.turnStartedAtMs = null;
    this.speechEndedAtMs = null;
    this.confirmationAwaitingVoice = false;
    this.confirmationPromptPlaybackCompleted = false;
    this.confirmationTurnStartInFlight = false;
    this.stopMicrophoneSafely().catch(() => undefined);

    const shouldReconnect = this.desiredConnection && !this.explicitStop;
    this.desiredSession = shouldReconnect;
    this.allowAutoReconnect = shouldReconnect;
    this.setSnapshot({
      connection: shouldReconnect ? 'reconnecting' : 'failed',
      session: 'idle',
      turn: 'idle',
      heartbeat: 'unknown',
      sessionId: null,
      turnId: null,
      responseId: null,
      confirmationAwaitingVoice: false,
      speechDetected: false,
      waitPhrase: null,
      followUpQueued: false,
      transcriptMessages: [],
      transcriptError: null,
      lastHeartbeatAtMs: null,
      error: shouldReconnect
        ? 'Voice authentication expired. Refreshing and reconnecting.'
        : 'Voice authentication expired. Start a new session.',
    });

    if (shouldReconnect && !this.reconnectTimer) {
      this.scheduleReconnect('authentication-expired');
    }
  }

  private finalizeResponseIfReady(): void {
    if (
      !this.responseServerCompleted ||
      !this.ttsPlaybackTerminal ||
      this.bargeInInFlight
    ) {
      return;
    }

    this.retireCorrelation(
      null,
      this.snapshot.turnId,
      this.snapshot.responseId,
    );
    this.turnStartedAtMs = null;
    this.setSnapshot({
      turn: 'completed',
      turnId: null,
      responseId: null,
      session: 'ready',
      waitPhrase: null,
    });
    this.setSnapshot({ turn: 'idle' });
    if (
      this.continuousListening &&
      !this.autoListenSuppressed &&
      !this.confirmationAwaitingVoice
    ) {
      this.scheduleContinuousListen(true);
    } else {
      this.stopMicrophoneSafely().catch(() => undefined);
    }
  }

  private scheduleContinuousListen(preserveMicrophone = false): void {
    if (
      !this.continuousListening ||
      this.autoListenSuppressed ||
      this.confirmationAwaitingVoice ||
      this.confirmationTurnStartInFlight ||
      this.snapshot.connection !== 'connected' ||
      this.snapshot.heartbeat !== 'healthy' ||
      this.snapshot.session !== 'ready' ||
      this.snapshot.turn !== 'idle' ||
      this.autoListenTimer
    ) {
      return;
    }
    this.autoListenTimer = setTimeout(() => {
      this.autoListenTimer = null;
      if (
        this.autoListenSuppressed ||
        this.snapshot.turn !== 'idle' ||
        this.snapshot.session !== 'ready' ||
        this.snapshot.connection !== 'connected' ||
        this.snapshot.heartbeat !== 'healthy'
      ) {
        return;
      }
      this.startTurn({
        preserveMicrophone,
        autoCommitOnSpeechEnd: true,
      }).catch(error => {
        this.setSnapshot({ error: safeVoiceError(error) });
      });
    }, 0);
  }

  private clearAutoListenTimer(): void {
    if (this.autoListenTimer) {
      clearTimeout(this.autoListenTimer);
      this.autoListenTimer = null;
    }
  }

  private clearSpeechEndCommitTimer(): void {
    if (this.speechEndCommitTimer) {
      clearTimeout(this.speechEndCommitTimer);
      this.speechEndCommitTimer = null;
    }
  }

  private scheduleSpeechEndCommit(): void {
    this.clearSpeechEndCommitTimer();
    if (!this.autoCommitBargeInTurn) {
      return;
    }
    this.speechEndCommitTimer = setTimeout(() => {
      this.speechEndCommitTimer = null;
      if (
        !this.autoCommitBargeInTurn ||
        this.snapshot.turn !== 'recording' ||
        this.snapshot.speechDetected
      ) {
        return;
      }
      this.commitBargeInTurn().catch(() => undefined);
    }, SPEECH_END_COMMIT_GRACE_MS);
  }

  private maybeStartVoiceConfirmationTurn(): void {
    if (
      !this.confirmationAwaitingVoice ||
      this.confirmationTurnStartInFlight ||
      this.snapshot.connection !== 'connected' ||
      this.snapshot.session !== 'ready' ||
      this.snapshot.turn !== 'idle' ||
      !this.responseServerCompleted ||
      !this.ttsPlaybackTerminal ||
      !this.confirmationPromptPlaybackCompleted
    ) {
      return;
    }

    this.confirmationTurnStartInFlight = true;
    console.info('VOICE_CONFIRMATION_TURN_STARTING', {
      sessionId: this.snapshot.sessionId,
      timestampMs: this.now(),
    });
    this.startTurn({ autoCommitOnSpeechEnd: true })
      .catch(error => {
        console.warn('VOICE_CONFIRMATION_TURN_START_FAILED', {
          message: safeVoiceError(error),
          timestampMs: this.now(),
        });
      })
      .finally(() => {
        this.confirmationTurnStartInFlight = false;
      });
  }

  private canBargeInDuringPlayback(): boolean {
    return (
      Boolean(this.snapshot.turnId && this.snapshot.responseId) &&
      this.snapshot.ttsPlaybackState === 'speaking' &&
      ['committing', 'waiting'].includes(this.snapshot.turn) &&
      !this.confirmationAwaitingVoice &&
      !this.bargeInInFlight
    );
  }

  private shouldBargeIn(eventType: string): boolean {
    return (
      eventType === 'SILERO_VAD_SPEECH_STARTED' &&
      this.canBargeInDuringPlayback()
    );
  }

  private playbackBargeInDecision(event: Record<string, unknown>) {
    const rawTimestamp = event.timestampMs;
    const candidateAtMs =
      typeof rawTimestamp === 'number' && Number.isFinite(rawTimestamp)
        ? rawTimestamp
        : this.now();
    const rawProbability = event.probability;
    const probability =
      typeof rawProbability === 'number' && Number.isFinite(rawProbability)
        ? rawProbability
        : null;
    const echoLikely =
      typeof event.echoLikely === 'boolean' ? event.echoLikely : null;
    const echoSimilarity =
      typeof event.echoSimilarity === 'number' &&
      Number.isFinite(event.echoSimilarity)
        ? event.echoSimilarity
        : null;
    const playbackReferenceAvailable =
      typeof event.playbackReferenceAvailable === 'boolean'
        ? event.playbackReferenceAvailable
        : null;
    const playbackState =
      typeof event.playbackState === 'string' ? event.playbackState : null;
    return evaluatePlaybackBargeIn({
      playbackActive:
        this.snapshot.ttsPlaybackState === 'speaking' ||
        event.playbackActive === true,
      playbackStartedAtMs: this.ttsPlaybackStartedAtMs,
      candidateAtMs,
      probability,
      echoLikely,
      echoSimilarity,
      playbackReferenceAvailable,
      playbackState,
    });
  }

  private async interruptForBargeIn(): Promise<void> {
    if (
      this.bargeInInFlight ||
      !this.shouldBargeIn('SILERO_VAD_SPEECH_STARTED')
    ) {
      return;
    }

    const turnId = this.snapshot.turnId;
    const responseId = this.snapshot.responseId;
    if (!turnId || !responseId) {
      return;
    }

    this.bargeInInFlight = true;
    console.info('BARGE_IN_REAL_SPEECH_CONFIRMED', {
      oldTurnId: turnId,
      oldResponseId: responseId,
      timestampMs: this.now(),
    });
    console.info('BARGE_IN_CONFIRMED', {
      oldTurnId: turnId,
      oldResponseId: responseId,
      timestampMs: this.now(),
    });
    emitLatencyTrace({
      sessionId: this.snapshot.sessionId,
      turnId,
      responseId,
      component: 'client',
      event: 'barge_in_confirmed',
    });
    // Retire the old correlation before any asynchronous work so late text
    // deltas and audio chunks cannot leak into the new user turn.
    this.retireCorrelation(null, turnId, responseId);
    this.applyConversationEvent({
      type: 'assistant.response.cancelled',
      sessionId: this.snapshot.sessionId,
      turnId,
      responseId,
      timestampMs: this.now(),
    });
    this.markCurrentTranscriptCancelled();
    this.setSnapshot({
      turn: 'cancelled',
      speechDetected: false,
      ttsPlaybackState: 'stopping',
      ttsError: null,
      error: null,
    });

    try {
      // Local audio must stop before the network cancellation to prevent the
      // old response from speaking over the newly detected user turn.
      try {
        await this.adapter.stopPlayback?.();
      } catch {
        // A local player failure must not leave the server response running.
        // The cancellation below is still sent, while the UI is reset to the
        // new-turn state after the old response is retired.
      } finally {
        this.ttsPlaybackTerminal = true;
        this.setSnapshot({
          ttsPlaybackState: 'idle',
          ttsResponseId: null,
        });
        console.info('LOCAL_TTS_STOPPED', {
          oldResponseId: responseId,
          timestampMs: this.now(),
        });
        emitLatencyTrace({
          sessionId: this.snapshot.sessionId,
          turnId,
          responseId,
          component: 'android',
          event: 'barge_in_playback_stopped',
        });
      }

      // Queue the old-response cancellation before the new turn control
      // message, but do not await the server acknowledgement. The native
      // transport retains the microphone and buffers the interruption while
      // the server finishes cancelling the old response.
      const cancellation = this.adapter.cancelResponse('barge_in');
      cancellation
        .then(() => {
          console.info('CLIENT_RESPONSE_CANCEL_SENT', {
            oldResponseId: responseId,
            timestampMs: this.now(),
          });
          emitLatencyTrace({
            sessionId: this.snapshot.sessionId,
            turnId,
            responseId,
            component: 'client',
            event: 'response_cancel_sent',
            metadata: { reason: 'barge_in' },
          });
        })
        .catch(error => {
          this.setSnapshot({ error: safeVoiceError(error) });
        });

      this.responseServerCompleted = false;
      this.ttsPlaybackTerminal = true;
      this.turnStartedAtMs = null;
      this.speechEndedAtMs = null;
      this.autoCommitBargeInTurn = true;
      this.bargeInSpeechEndedPending = false;
      this.setSnapshot({
        turn: 'idle',
        turnId: null,
        responseId: null,
        session: 'ready',
        speechDetected: false,
        ttsPlaybackState: 'idle',
        ttsResponseId: null,
        ttsError: null,
      });
      console.info('BARGE_IN_NEW_TURN_PENDING', {
        oldTurnId: turnId,
        oldResponseId: responseId,
        timestampMs: this.now(),
      });
      await this.startTurn({
        preserveMicrophone: true,
        includePreRoll: true,
        autoCommitOnSpeechEnd: true,
      });
    } catch (error) {
      this.autoCommitBargeInTurn = false;
      this.bargeInSpeechEndedPending = false;
      this.setSnapshot({
        turn: 'failed',
        speechDetected: false,
        error: safeVoiceError(error),
      });
    } finally {
      this.bargeInInFlight = false;
    }
  }

  private handleVadEvent(input: unknown): void {
    if (!input || typeof input !== 'object' || Array.isArray(input)) {
      return;
    }
    const event = input as Record<string, unknown>;
    const eventType = typeof event.event === 'string' ? event.event : '';
    if (!eventType) {
      return;
    }
    const nativeMonotonicNs =
      typeof event.monotonicNs === 'string' && /^\d+$/.test(event.monotonicNs)
        ? Number(event.monotonicNs)
        : null;
    const nativeMonotonicMs =
      nativeMonotonicNs != null && Number.isFinite(nativeMonotonicNs)
        ? nativeMonotonicNs / 1_000_000
        : null;
    const isSileroSpeechEvent =
      eventType === 'SILERO_VAD_SPEECH_STARTED' ||
      eventType === 'SILERO_VAD_SPEECH_ACTIVITY';
    if (
      eventType === 'SILERO_VAD_SPEECH_STARTED' ||
      eventType === 'VAD_SPEECH_STARTED'
    ) {
      emitLatencyTrace({
        sessionId: this.snapshot.sessionId,
        turnId: this.snapshot.turnId,
        responseId: this.snapshot.responseId,
        component: 'android',
        event: 'microphone_speech_start',
        monotonicNs: nativeMonotonicNs,
        monotonicMs: nativeMonotonicMs,
        clockDomain:
          nativeMonotonicNs == null ? 'client' : 'android_elapsed_realtime',
        metadata: {
          probability: event.probability,
          speech_duration_ms: event.speechDurationMs,
          reason: event.reason,
        },
      });
    } else if (
      eventType === 'SILERO_VAD_SPEECH_STOPPED' ||
      eventType === 'VAD_SPEECH_STOPPED'
    ) {
      emitLatencyTrace({
        sessionId: this.snapshot.sessionId,
        turnId: this.snapshot.turnId,
        responseId: this.snapshot.responseId,
        component: 'android',
        event: 'vad_end',
        monotonicNs: nativeMonotonicNs,
        monotonicMs: nativeMonotonicMs,
        clockDomain:
          nativeMonotonicNs == null ? 'client' : 'android_elapsed_realtime',
        metadata: {
          speech_duration_ms: event.speechDurationMs,
          reason: event.reason,
        },
      });
    }
    if (this.shouldBargeIn(eventType)) {
      const decision = this.playbackBargeInDecision(event);
      const candidateAtMs =
        typeof event.timestampMs === 'number' &&
        Number.isFinite(event.timestampMs)
          ? event.timestampMs
          : this.now();
      const speechDurationMs =
        typeof event.speechDurationMs === 'number' &&
        Number.isFinite(event.speechDurationMs)
          ? event.speechDurationMs
          : 0;
      // Native SILERO_VAD_SPEECH_STARTED is emitted only after the native
      // 160 ms confirmation window. Its event payload intentionally reports
      // duration 0, so use the configured confirmation duration for the JS
      // decision while preserving the raw payload in diagnostics.
      const effectiveSpeechDurationMs =
        eventType === 'SILERO_VAD_SPEECH_STARTED'
          ? Math.max(speechDurationMs, 160)
          : speechDurationMs;
      if (eventType === 'SILERO_VAD_SPEECH_STARTED') {
        this.sileroSpeechSegmentStartedAtMs = candidateAtMs;
        this.sileroSpeechSegmentStartedDuringGuard = !decision.accepted;
      }
      emitLatencyTrace({
        sessionId: this.snapshot.sessionId,
        turnId: this.snapshot.turnId,
        responseId: this.snapshot.responseId,
        component: 'client',
        event: 'barge_in_candidate',
        metadata: {
          probability: event.probability,
          playback_age_ms: decision.playbackAgeMs,
          speech_duration_ms: effectiveSpeechDurationMs,
          accepted: decision.accepted,
          reason: decision.reason,
          event_type: eventType,
        },
      });
      console.info('BARGE_IN_CANDIDATE', {
        reason: 'silero',
        probability: event.probability ?? null,
        playbackActive: true,
        playbackAgeMs: decision.playbackAgeMs,
        playbackStartGuardMs: PLAYBACK_START_GUARD_MS,
        playbackProbabilityThreshold: PLAYBACK_SPEECH_PROBABILITY_THRESHOLD,
        elapsed_since_tts_start_ms: decision.playbackAgeMs,
        speech_duration_ms: effectiveSpeechDurationMs,
        eventType,
        timestampMs: this.now(),
      });
      console.info('BARGE_IN_GUARD_CHECK', {
        guard_active:
          decision.playbackAgeMs !== null &&
          decision.playbackAgeMs < PLAYBACK_START_GUARD_MS,
        elapsed_since_tts_start_ms: decision.playbackAgeMs,
        timestampMs: this.now(),
      });
      const probability =
        typeof event.probability === 'number' &&
        Number.isFinite(event.probability)
          ? event.probability
          : null;
      console.info('BARGE_IN_THRESHOLD_CHECK', {
        probability,
        required: PLAYBACK_SPEECH_PROBABILITY_THRESHOLD,
        passed:
          probability !== null &&
          probability >= PLAYBACK_SPEECH_PROBABILITY_THRESHOLD,
        timestampMs: this.now(),
      });
      console.info('PLAYBACK_ECHO_THRESHOLD_CHECK', {
        echoSimilarity: decision.echoSimilarity,
        required: PLAYBACK_ECHO_SIMILARITY_THRESHOLD,
        passed:
          decision.echoSimilarity !== null &&
          decision.echoSimilarity >= PLAYBACK_ECHO_SIMILARITY_THRESHOLD,
        timestampMs: this.now(),
      });
      const continuationOfGuardSegment =
        eventType === 'SILERO_VAD_SPEECH_ACTIVITY' &&
        this.sileroSpeechSegmentStartedDuringGuard;
      if (!decision.accepted || continuationOfGuardSegment) {
        if (decision.reason === 'likely_playback_echo') {
          console.info('BARGE_IN_ECHO_REJECTED', {
            responseId: this.snapshot.responseId,
            candidateStartMs: candidateAtMs,
            candidateDurationMs: effectiveSpeechDurationMs,
            echoSimilarity: decision.echoSimilarity,
            playbackPositionMs: event.playbackPositionMs ?? null,
            timestampMs: this.now(),
          });
          emitLatencyTrace({
            sessionId: this.snapshot.sessionId,
            turnId: this.snapshot.turnId,
            responseId: this.snapshot.responseId,
            component: 'client',
            event: 'barge_in_echo_rejected',
            metadata: {
              candidate_start_ms: candidateAtMs,
              candidate_duration_ms: effectiveSpeechDurationMs,
              echo_similarity: decision.echoSimilarity,
              playback_position_ms: event.playbackPositionMs,
            },
          });
        }
        console.info('BARGE_IN_REJECTED', {
          reason: continuationOfGuardSegment
            ? 'segment_started_during_playback_guard'
            : decision.reason,
          probability: event.probability ?? null,
          playbackActive: true,
          playbackAgeMs: decision.playbackAgeMs,
          speech_duration_ms: effectiveSpeechDurationMs,
          timestampMs: this.now(),
        });
        if (eventType === 'SILERO_VAD_SPEECH_STARTED') {
          console.info('BARGE_IN_CONFIRM_TIMER_CANCELLED', {
            reason: decision.reason,
            timestampMs: this.now(),
          });
        }
        return;
      }
      if (eventType === 'SILERO_VAD_SPEECH_STARTED') {
        console.info('BARGE_IN_CONFIRM_TIMER_STARTED', {
          required_ms: PLAYBACK_BARGE_IN_CONFIRMATION_MS,
          speech_duration_ms: effectiveSpeechDurationMs,
          source: 'native_silero_confirmation',
          timestampMs: this.now(),
        });
        // Native SPEECH_STARTED is only the first confirmed VAD transition.
        // Wait for a later activity event so one speaker-echo transition cannot
        // cancel an otherwise healthy response.
        return;
      }
      console.info('BARGE_IN_REAL_SPEECH_CONFIRMED', {
        responseId: this.snapshot.responseId,
        echoSimilarity: decision.echoSimilarity,
        playbackPositionMs: event.playbackPositionMs ?? null,
        timestampMs: this.now(),
      });
      console.info('BARGE_IN_SPEECH_DETECTED', {
        responseId: this.snapshot.responseId,
        timestampMs: this.now(),
      });
      this.interruptForBargeIn().catch(() => undefined);
      return;
    }
    if (
      eventType === 'SILERO_VAD_SPEECH_ACTIVITY' &&
      this.canBargeInDuringPlayback()
    ) {
      const decision = this.playbackBargeInDecision(event);
      const candidateAtMs =
        typeof event.timestampMs === 'number' &&
        Number.isFinite(event.timestampMs)
          ? event.timestampMs
          : this.now();
      const speechDurationMs =
        typeof event.speechDurationMs === 'number' &&
        Number.isFinite(event.speechDurationMs)
          ? event.speechDurationMs
          : 0;
      const speechSegmentAgeMs =
        this.sileroSpeechSegmentStartedAtMs === null
          ? 0
          : Math.max(0, candidateAtMs - this.sileroSpeechSegmentStartedAtMs);
      const sustainedSpeechMs = Math.max(speechDurationMs, speechSegmentAgeMs);
      const continuationOfGuardSegment =
        this.sileroSpeechSegmentStartedDuringGuard;
      const confirmed =
        decision.accepted &&
        !continuationOfGuardSegment &&
        this.sileroSpeechSegmentStartedAtMs !== null &&
        sustainedSpeechMs >= PLAYBACK_BARGE_IN_CONFIRMATION_MS;
      emitLatencyTrace({
        sessionId: this.snapshot.sessionId,
        turnId: this.snapshot.turnId,
        responseId: this.snapshot.responseId,
        component: 'client',
        event: 'barge_in_candidate',
        metadata: {
          probability: event.probability,
          playback_age_ms: decision.playbackAgeMs,
          speech_duration_ms: sustainedSpeechMs,
          accepted: confirmed,
          reason: confirmed
            ? 'near_end_speech'
            : continuationOfGuardSegment
            ? 'segment_started_during_playback_guard'
            : decision.reason,
          event_type: eventType,
        },
      });
      console.info('BARGE_IN_ACTIVITY_CHECK', {
        probability: event.probability ?? null,
        playbackAgeMs: decision.playbackAgeMs,
        speechDurationMs,
        speechSegmentAgeMs,
        sustainedSpeechMs,
        requiredMs: PLAYBACK_BARGE_IN_CONFIRMATION_MS,
        continuationOfGuardSegment,
        decision: decision.reason,
        confirmed,
        timestampMs: this.now(),
      });
      if (!confirmed) {
        if (decision.reason === 'likely_playback_echo') {
          console.info('BARGE_IN_ECHO_REJECTED', {
            responseId: this.snapshot.responseId,
            candidateStartMs: this.sileroSpeechSegmentStartedAtMs,
            candidateDurationMs: sustainedSpeechMs,
            echoSimilarity: decision.echoSimilarity,
            playbackPositionMs: event.playbackPositionMs ?? null,
            timestampMs: this.now(),
          });
          emitLatencyTrace({
            sessionId: this.snapshot.sessionId,
            turnId: this.snapshot.turnId,
            responseId: this.snapshot.responseId,
            component: 'client',
            event: 'barge_in_echo_rejected',
            metadata: {
              candidate_start_ms: this.sileroSpeechSegmentStartedAtMs,
              candidate_duration_ms: sustainedSpeechMs,
              echo_similarity: decision.echoSimilarity,
              playback_position_ms: event.playbackPositionMs,
            },
          });
        }
        console.info('BARGE_IN_REJECTED', {
          reason: continuationOfGuardSegment
            ? 'segment_started_during_playback_guard'
            : sustainedSpeechMs < PLAYBACK_BARGE_IN_CONFIRMATION_MS
            ? 'speech_confirmation_incomplete'
            : decision.reason,
          probability: event.probability ?? null,
          playbackAgeMs: decision.playbackAgeMs,
          speech_duration_ms: sustainedSpeechMs,
          timestampMs: this.now(),
        });
        return;
      }
      console.info('BARGE_IN_REAL_SPEECH_CONFIRMED', {
        responseId: this.snapshot.responseId,
        echoSimilarity: decision.echoSimilarity,
        playbackPositionMs: event.playbackPositionMs ?? null,
        timestampMs: this.now(),
      });
      console.info('BARGE_IN_SPEECH_DETECTED', {
        responseId: this.snapshot.responseId,
        timestampMs: this.now(),
      });
      this.interruptForBargeIn().catch(() => undefined);
      return;
    }
    if (isSileroSpeechEvent && this.snapshot.ttsPlaybackState === 'speaking') {
      const decision = this.playbackBargeInDecision(event);
      console.info('BARGE_IN_CANDIDATE', {
        reason: 'silero_not_eligible_state',
        probability: event.probability ?? null,
        playbackActive: true,
        playbackAgeMs: decision.playbackAgeMs,
        elapsed_since_tts_start_ms: decision.playbackAgeMs,
        speech_duration_ms: event.speechDurationMs ?? 0,
        eventType,
        timestampMs: this.now(),
      });
      console.info('BARGE_IN_REJECTED', {
        reason: 'client_turn_state_not_interruptible',
        playbackAgeMs: decision.playbackAgeMs,
        timestampMs: this.now(),
      });
    }
    if (!this.snapshot.turnId) {
      if (
        eventType === 'VAD_SPEECH_STARTED' ||
        eventType === 'SILERO_VAD_SPEECH_STARTED'
      ) {
        this.clearSpeechEndCommitTimer();
        this.bargeInSpeechEndedPending = false;
        this.speechEndedAtMs = null;
      }
      if (
        this.autoCommitBargeInTurn &&
        (eventType === 'VAD_SPEECH_STOPPED' ||
          eventType === 'SILERO_VAD_SPEECH_STOPPED')
      ) {
        this.bargeInSpeechEndedPending = true;
        this.speechEndedAtMs = this.now();
        console.info('BARGE_IN_SPEECH_END', {
          turnId: this.bargeInTurnId,
          timestampMs: this.speechEndedAtMs,
          pendingTurnId: true,
        });
      }
      return;
    }
    if (
      eventType === 'VAD_SPEECH_STARTED' ||
      eventType === 'SILERO_VAD_SPEECH_STARTED'
    ) {
      this.clearSpeechEndCommitTimer();
      this.speechEndedAtMs = null;
      if (
        !['starting', 'recording', 'speech_detected'].includes(
          this.snapshot.turn,
        )
      ) {
        return;
      }
      this.markCurrentTranscriptPhase('speech_detected');
      this.setSnapshot({ turn: 'speech_detected', speechDetected: true });
      return;
    }
    if (
      eventType === 'VAD_SPEECH_STOPPED' ||
      eventType === 'SILERO_VAD_SPEECH_STOPPED'
    ) {
      if (eventType === 'SILERO_VAD_SPEECH_STOPPED') {
        this.sileroSpeechSegmentStartedAtMs = null;
        this.sileroSpeechSegmentStartedDuringGuard = false;
      }
      if (!['recording', 'speech_detected'].includes(this.snapshot.turn)) {
        return;
      }
      this.speechEndedAtMs = this.now();
      this.markCurrentTranscriptSpeechEnded();
      this.markCurrentTranscriptPhase('listening');
      this.setSnapshot({ turn: 'recording', speechDetected: false });
      if (this.autoCommitBargeInTurn) {
        console.info('BARGE_IN_SPEECH_END', {
          turnId: this.snapshot.turnId,
          timestampMs: this.speechEndedAtMs,
          commit_grace_ms: SPEECH_END_COMMIT_GRACE_MS,
        });
        this.scheduleSpeechEndCommit();
      }
    }
  }

  private async commitBargeInTurn(): Promise<void> {
    if (this.bargeInCommitInFlight || !this.autoCommitBargeInTurn) {
      return;
    }
    this.bargeInCommitInFlight = true;
    try {
      console.info('NEW_TURN_COMMITTED', {
        turnId: this.snapshot.turnId,
        timestampMs: this.now(),
      });
      await this.commitTurn();
    } catch (error) {
      this.setSnapshot({
        turn: 'failed',
        error: safeVoiceError(error),
      });
    } finally {
      this.autoCommitBargeInTurn = false;
      this.bargeInCommitInFlight = false;
    }
  }

  private applyConversationEvent(event: VoiceEvent): void {
    if (event.type === 'assistant.text.delta') {
      this.pendingConversationEvents.push(event);
      if (!this.conversationFlushTimer) {
        this.conversationFlushTimer = setTimeout(() => {
          this.conversationFlushTimer = null;
          this.flushConversationEvents();
        }, 16);
      }
      return;
    }

    this.flushConversationEvents();
    const result = reduceVoiceEvent(this.conversationState, event, this.now());
    if (result.accepted) {
      this.publishConversation(result.state);
    }
  }

  private flushConversationEvents(): void {
    if (this.conversationFlushTimer) {
      clearTimeout(this.conversationFlushTimer);
      this.conversationFlushTimer = null;
    }
    if (!this.pendingConversationEvents.length) {
      return;
    }

    let state = this.conversationState;
    for (const event of this.pendingConversationEvents) {
      const result = reduceVoiceEvent(state, event, this.now());
      if (result.accepted) {
        state = result.state;
      }
    }
    this.pendingConversationEvents = [];
    if (state !== this.conversationState) {
      this.publishConversation(state);
    }
  }

  private clearConversationState(): void {
    if (this.conversationFlushTimer) {
      clearTimeout(this.conversationFlushTimer);
      this.conversationFlushTimer = null;
    }
    this.pendingConversationEvents = [];
    this.conversationState = clearConversation();
    this.setSnapshot({
      conversationMessages: [],
      firstTextAtMs: null,
      conversationRenderCompletedAtMs: null,
    });
  }

  private publishConversation(state: ConversationState): void {
    this.conversationState = state;
    const latestAssistant = [...state.messages]
      .reverse()
      .find(
        (message): message is ConversationAssistantMessage =>
          message.role === 'assistant' && message.firstTextAtMs !== null,
      );
    this.setSnapshot({
      conversationMessages: state.messages,
      firstTextAtMs: latestAssistant?.firstTextAtMs ?? null,
      conversationRenderCompletedAtMs:
        latestAssistant?.renderCompletedAtMs ?? null,
    });
  }

  private canAcceptTranscript(event: CanonicalTranscriptEvent): boolean {
    if (
      !event.turnId ||
      event.turnId !== this.snapshot.turnId ||
      this.snapshot.turn === 'cancelled'
    ) {
      return false;
    }
    const message = this.snapshot.transcriptMessages.find(
      candidate => candidate.turnId === event.turnId,
    );
    if (!message || message.final || message.status === 'cancelled') {
      return !message;
    }
    return !(
      event.sequence !== null &&
      message.transcriptSequence !== null &&
      event.sequence <= message.transcriptSequence
    );
  }

  private ensureTranscriptPlaceholder(
    phase: 'listening' | 'speech_detected' | 'transcribing',
  ): void {
    const turnId = this.snapshot.turnId;
    if (!turnId) {
      return;
    }
    const messages = createTranscriptPlaceholder(
      this.snapshot.transcriptMessages,
      turnId,
      this.snapshot.responseId,
      this.now(),
    );
    this.setSnapshot({
      transcriptMessages: markTranscriptPhase(
        messages,
        turnId,
        phase,
        this.now(),
      ),
      transcriptError: null,
    });
    const nextConversation = ensureConversationUserPlaceholder(
      this.conversationState,
      turnId,
      this.snapshot.responseId,
      this.now(),
    );
    if (nextConversation !== this.conversationState) {
      this.publishConversation(nextConversation);
    }
  }

  private handleTranscriptEvent(event: CanonicalTranscriptEvent): void {
    const result = applyTranscriptEvent(
      this.snapshot.transcriptMessages,
      event,
      this.now(),
    );
    if (!result.accepted) {
      this.recordDroppedEvent(false);
      return;
    }
    const message = result.messages.find(
      candidate => candidate.turnId === event.turnId,
    );
    const isFinal = event.kind === 'final' && message?.final;
    this.setSnapshot({
      transcriptMessages: result.messages,
      ...(isFinal ? { transcriptError: null, error: null } : {}),
      ...(event.kind === 'final' && !message?.final
        ? { transcriptError: mapTranscriptError('stt_empty_transcript') }
        : {}),
    });
    if (event.kind === 'partial') {
      this.applyConversationEvent({
        type: 'user.transcript.partial',
        sessionId: event.sessionId,
        turnId: event.turnId,
        responseId: event.responseId,
        text: event.text,
        sequence: event.sequence,
        timestampMs: event.timestampMs,
      });
    } else if (message?.final && event.text.trim()) {
      if (event.turnId === this.bargeInTurnId) {
        console.info('STT_FINAL', {
          turnId: event.turnId,
          text: event.text,
          timestampMs: this.now(),
        });
      }
      this.applyConversationEvent({
        type: 'user.transcript.final',
        sessionId: event.sessionId,
        turnId: event.turnId,
        responseId: event.responseId,
        text: event.text,
        sequence: event.sequence,
        timestampMs: event.timestampMs,
      });
      this.applyConversationEvent({
        type: 'assistant.request.started',
        sessionId: event.sessionId,
        turnId: event.turnId,
        responseId: event.responseId,
        sequence: null,
        retry: false,
        timestampMs: event.timestampMs,
      });
    }
    if (event.kind === 'final' && !message?.final) {
      const emptyError = mapTranscriptError('stt_empty_transcript');
      this.setSnapshot({
        turn: 'failed',
        speechDetected: false,
        error: emptyError.message,
        transcriptError: emptyError,
      });
    }
  }

  private markCurrentTranscriptPhase(
    phase: 'listening' | 'speech_detected' | 'transcribing',
  ): void {
    this.setSnapshot({
      transcriptMessages: markTranscriptPhase(
        this.snapshot.transcriptMessages,
        this.snapshot.turnId,
        phase,
        this.now(),
      ),
    });
    const nextConversation = markConversationUserPhase(
      this.conversationState,
      this.snapshot.turnId,
      phase,
    );
    if (nextConversation !== this.conversationState) {
      this.publishConversation(nextConversation);
    }
  }

  private markCurrentTranscriptSpeechEnded(): void {
    this.setSnapshot({
      transcriptMessages: markTranscriptSpeechEnded(
        this.snapshot.transcriptMessages,
        this.snapshot.turnId,
        this.speechEndedAtMs ?? this.now(),
      ),
    });
  }

  private markCurrentTranscriptError(error: unknown): void {
    const code =
      typeof error === 'string'
        ? error.match(
            /(?:^|\s)(stt_[a-z_]+|voice_turn_timeout)(?::|\s|$)/i,
          )?.[1]
        : error instanceof Error
        ? error.message.match(
            /(?:^|\s)(stt_[a-z_]+|voice_turn_timeout)(?::|\s|$)/i,
          )?.[1]
        : null;
    const mapped = mapTranscriptError(code);
    this.setSnapshot({
      transcriptMessages: markTranscriptError(
        this.snapshot.transcriptMessages,
        this.snapshot.turnId,
        code,
      ),
      transcriptError: mapped,
    });
  }

  private markCurrentTranscriptCancelled(): void {
    this.setSnapshot({
      transcriptMessages: markTranscriptCancelled(
        this.snapshot.transcriptMessages,
        this.snapshot.turnId,
      ),
    });
  }

  private isStaleEvent(event: NormalizedVoiceEvent): boolean {
    if (
      this.snapshot.connection !== 'connected' &&
      !EVENTS_ALLOWED_DURING_CONNECTION_TRANSITION.has(event.type)
    ) {
      return true;
    }
    if (event.sessionId && this.retiredSessionIds.has(event.sessionId)) {
      return true;
    }
    if (
      event.turnId &&
      TURN_SCOPED_EVENTS.has(event.type) &&
      this.retiredTurnIds.has(event.turnId)
    ) {
      return true;
    }
    if (
      event.responseId &&
      RESPONSE_SCOPED_EVENTS.has(event.type) &&
      this.retiredResponseIds.has(event.responseId)
    ) {
      return true;
    }
    if (
      event.sessionId &&
      this.snapshot.sessionId &&
      event.sessionId !== this.snapshot.sessionId
    ) {
      return true;
    }
    if (
      event.responseId &&
      this.snapshot.responseId &&
      event.responseId !== this.snapshot.responseId &&
      RESPONSE_SCOPED_EVENTS.has(event.type)
    ) {
      return true;
    }
    if (
      event.turnId &&
      this.snapshot.turnId &&
      event.turnId !== this.snapshot.turnId &&
      TURN_SCOPED_EVENTS.has(event.type)
    ) {
      return true;
    }
    return false;
  }

  private rememberEvent(key: string, hasEventId: boolean): void {
    const target = hasEventId ? this.seenEventIds : this.seenFallbackEvents;
    target.add(key);
    while (target.size > MAX_TRACKED_EVENT_IDS) {
      const first = target.values().next().value as string | undefined;
      if (!first) {
        break;
      }
      target.delete(first);
    }
  }

  private retireCurrentTurnCorrelation(): void {
    this.retireCorrelation(
      null,
      this.snapshot.turnId,
      this.snapshot.responseId,
    );
  }

  private retireCorrelation(
    sessionId: string | null,
    turnId: string | null,
    responseId: string | null,
  ): void {
    this.rememberId(this.retiredSessionIds, sessionId);
    this.rememberId(this.retiredTurnIds, turnId);
    this.rememberId(this.retiredResponseIds, responseId);
  }

  private rememberId(target: Set<string>, value: string | null): void {
    if (!value) {
      return;
    }
    target.add(value);
    while (target.size > MAX_TRACKED_EVENT_IDS) {
      const first = target.values().next().value as string | undefined;
      if (!first) {
        break;
      }
      target.delete(first);
    }
  }

  private recordDroppedEvent(invalid: boolean): void {
    this.setSnapshot({
      droppedEventCount: this.snapshot.droppedEventCount + 1,
      ...(invalid
        ? { invalidEventCount: this.snapshot.invalidEventCount + 1 }
        : {}),
    });
  }

  private suppressAudioForTransportFailure(reason: string): void {
    this.clearAutoListenTimer();
    this.clearSpeechEndCommitTimer();
    this.autoListenSuppressed = true;
    this.sileroSpeechSegmentStartedAtMs = null;
    this.sileroSpeechSegmentStartedDuringGuard = false;
    this.bargeInSpeechEndedPending = false;
    this.autoCommitBargeInTurn = false;
    console.info('TRANSPORT_UNHEALTHY_AUDIO_SUPPRESSED', {
      reason,
      sessionId: this.snapshot.sessionId,
      turnId: this.snapshot.turnId,
      responseId: this.snapshot.responseId,
      timestampMs: this.now(),
    });
    emitLatencyTrace({
      sessionId: this.snapshot.sessionId,
      turnId: this.snapshot.turnId,
      responseId: this.snapshot.responseId,
      component: 'client',
      event: 'transport_unhealthy_audio_suppressed',
      metadata: { reason },
    });
    this.adapter.stopMicrophone?.().catch(() => undefined);
    if (
      ['buffering', 'speaking', 'stopping'].includes(
        this.snapshot.ttsPlaybackState,
      )
    ) {
      this.adapter.stopPlayback?.().catch(() => undefined);
    }
  }

  private checkHeartbeat(): void {
    if (!['connected', 'degraded'].includes(this.snapshot.connection)) {
      return;
    }
    const lastHeartbeatAtMs = this.snapshot.lastHeartbeatAtMs ?? this.now();
    if (this.now() - lastHeartbeatAtMs < this.heartbeatTimeoutMs) {
      return;
    }
    if (this.snapshot.heartbeat === 'missed') {
      return;
    }
    this.setSnapshot({
      connection: 'degraded',
      heartbeat: 'missed',
      error: 'Voice connection heartbeat was missed. Reconnecting…',
    });
    this.suppressAudioForTransportFailure('heartbeat_missed');
    if (this.desiredConnection && !this.explicitStop) {
      this.scheduleReconnect('heartbeat');
      this.closeTransportForReconnect().catch(() => undefined);
    }
  }

  private scheduleReconnect(reason: string): void {
    if (
      !this.started ||
      !this.desiredConnection ||
      this.explicitStop ||
      this.reconnectTimer
    ) {
      return;
    }
    if (this.appState.currentState !== 'active') {
      this.allowAutoReconnect = true;
      this.setSnapshot({
        connection: 'reconnecting',
        error: 'Voice connection will resume when the app is active.',
      });
      return;
    }
    if (this.snapshot.reconnectAttempt >= this.reconnectDelaysMs.length) {
      this.setSnapshot({
        connection: 'failed',
        error: 'Voice connection could not be restored. Retry when ready.',
      });
      return;
    }

    this.allowAutoReconnect = true;
    this.sessionStartInFlight = false;
    const reconnectAttempt = this.snapshot.reconnectAttempt + 1;
    const delayMs = this.reconnectDelaysMs[reconnectAttempt - 1] ?? 2_000;
    this.setSnapshot({
      connection: 'reconnecting',
      reconnectAttempt,
      error:
        reason === 'heartbeat'
          ? 'Voice connection is recovering…'
          : reason === 'stale-session'
          ? 'The previous voice session expired. Reconnecting safely…'
          : 'Voice connection lost. Reconnecting…',
    });
    this.reconnectTimer = setTimeout(() => {
      this.reconnectTimer = null;
      if (!this.desiredConnection || this.explicitStop) {
        return;
      }
      this.connectPromise = this.openTransport(true).finally(() => {
        this.connectPromise = null;
      });
      this.connectPromise.catch(() => undefined);
    }, delayMs);
  }

  private async closeTransportForReconnect(): Promise<void> {
    try {
      await this.adapter.disconnect();
    } catch {
      // The reconnect attempt will still be bounded if the old transport is gone.
    }
  }

  private clearReconnectTimer(): void {
    if (this.reconnectTimer) {
      clearTimeout(this.reconnectTimer);
      this.reconnectTimer = null;
    }
  }

  private resetToDisconnected(): void {
    this.clearAutoListenTimer();
    this.clearSpeechEndCommitTimer();
    this.clearConversationState();
    this.retireCorrelation(
      this.snapshot.sessionId,
      this.snapshot.turnId,
      this.snapshot.responseId,
    );
    this.sessionStartInFlight = false;
    this.turnStartedAtMs = null;
    this.responseServerCompleted = false;
    this.ttsPlaybackTerminal = true;
    this.autoListenSuppressed = false;
    this.bargeInInFlight = false;
    this.confirmationAwaitingVoice = false;
    this.confirmationPromptPlaybackCompleted = false;
    this.confirmationTurnStartInFlight = false;
    this.setSnapshot({
      connection: 'disconnected',
      session: 'idle',
      turn: 'idle',
      speechDetected: false,
      heartbeat: 'unknown',
      sessionId: null,
      turnId: null,
      responseId: null,
      confirmationAwaitingVoice: false,
      ttsPlaybackState: 'idle',
      ttsResponseId: null,
      ttsError: null,
      waitPhrase: null,
      followUpQueued: false,
      transcriptMessages: [],
      transcriptError: null,
      lastHeartbeatAtMs: null,
      reconnectAttempt: 0,
      error: null,
    });
  }

  private setSnapshot(patch: Partial<VoiceSocketSnapshot>): void {
    this.snapshot = { ...this.snapshot, ...patch };
    this.notify();
  }

  private notify(): void {
    this.listeners.forEach(listener => listener(this.snapshot));
  }
}

function readString(value: unknown, maxLength: number): string | null {
  if (
    typeof value !== 'string' ||
    value.length === 0 ||
    value.length > maxLength
  ) {
    return null;
  }
  return value;
}

function readTimestamp(value: unknown): number | null {
  return typeof value === 'number' && Number.isFinite(value) && value >= 0
    ? value
    : null;
}

function readNumber(value: unknown): number | null {
  return typeof value === 'number' && Number.isFinite(value) ? value : null;
}

function readBoolean(value: unknown): boolean | null {
  return typeof value === 'boolean' ? value : null;
}

function readAssistantEvent(
  type: string,
  record: Record<string, unknown>,
): VoiceEvent | null {
  const sessionId = readString(
    record.sessionId ?? record.session_id,
    MAX_ID_LENGTH,
  );
  const turnId = readString(record.turnId ?? record.turn_id, MAX_ID_LENGTH);
  const responseId = readString(
    record.responseId ?? record.response_id,
    MAX_ID_LENGTH,
  );
  const timestampMs = readTimestamp(record.timestampMs ?? record.timestamp_ms);
  const sequence = readSequence(record.sequence);

  if (
    type === 'assistant.response.started' ||
    type === 'assistant.request.started'
  ) {
    if (!turnId || !responseId) {
      return null;
    }
    return {
      type: 'assistant.request.started',
      sessionId,
      turnId,
      responseId,
      sequence,
      retry: readBoolean(record.retry) ?? false,
      timestampMs,
    };
  }

  if (type === 'assistant.text.delta') {
    const delta = readString(record.delta, MAX_TRANSCRIPT_LENGTH);
    if (!turnId || !responseId || !delta || sequence === null) {
      return null;
    }
    return {
      type: 'assistant.text.delta',
      sessionId,
      turnId,
      responseId,
      delta,
      sequence,
      timestampMs,
    };
  }

  if (type === 'assistant.text.final' || type === 'llm.response.completed') {
    const text = readString(record.text, MAX_TRANSCRIPT_LENGTH);
    if (!turnId || !responseId || !text) {
      return null;
    }
    return {
      type: 'assistant.text.completed',
      sessionId,
      turnId,
      responseId,
      text,
      sequence,
      metrics: readMetrics(record.metrics),
      timestampMs,
    };
  }

  if (type === 'assistant.response.failed' || type === 'llm.response.failed') {
    if (!turnId || !responseId) {
      return null;
    }
    const errorCode =
      readString(record.code ?? record.errorCode, MAX_ID_LENGTH) ??
      'llm_provider_error';
    return {
      type: 'assistant.response.failed',
      sessionId,
      turnId,
      responseId,
      errorCode,
      retryable:
        readBoolean(record.retryable) ??
        mapConversationError(errorCode).retryable,
      timestampMs,
    };
  }

  if (type === 'tool.status' || type === 'confirmation.required') {
    const toolCallId = readString(
      record.toolCallId ?? record.tool_call_id,
      MAX_ID_LENGTH,
    );
    const name = readString(record.toolName ?? record.tool_name, MAX_ID_LENGTH);
    const rawStatus =
      type === 'confirmation.required'
        ? 'confirmation_required'
        : readString(record.toolStatus ?? record.tool_status, 32);
    if (
      !turnId ||
      !responseId ||
      !toolCallId ||
      !name ||
      !isConversationToolStatus(rawStatus)
    ) {
      return null;
    }
    return {
      type: 'tool.status',
      sessionId,
      turnId,
      responseId,
      toolCallId,
      name,
      status: rawStatus,
      result: null,
      confirmationId: readString(
        record.confirmationId ?? record.confirmation_id,
        MAX_ID_LENGTH,
      ),
      errorCode: readString(
        record.errorCode ?? record.error_code,
        MAX_ID_LENGTH,
      ),
      timestampMs,
    };
  }

  return null;
}

function isConversationToolStatus(
  value: string | null,
): value is Extract<VoiceEvent, { type: 'tool.status' }>['status'] {
  return Boolean(
    value &&
      [
        'understanding',
        'confirmation_required',
        'approved',
        'executing',
        'success',
        'failed',
        'cancelled',
      ].includes(value),
  );
}

function readMetrics(value: unknown): Record<string, number | null> {
  if (!value || typeof value !== 'object' || Array.isArray(value)) {
    return {};
  }
  const metrics: Record<string, number | null> = {};
  for (const [key, metric] of Object.entries(value)) {
    if (Object.keys(metrics).length >= MAX_METRIC_COUNT) {
      break;
    }
    if (typeof metric === 'number' && Number.isFinite(metric)) {
      metrics[key.slice(0, MAX_METRIC_KEY_LENGTH)] = metric;
    } else if (metric === null) {
      metrics[key.slice(0, MAX_METRIC_KEY_LENGTH)] = null;
    }
  }
  return metrics;
}

function readTranscriptEvent(
  type: string,
  record: Record<string, unknown>,
): CanonicalTranscriptEvent | null {
  const kind =
    type === 'transcript.partial' || type === 'voice.transcript.partial'
      ? 'partial'
      : type === 'transcript.final' ||
        type === 'voice.transcript.final.delivered'
      ? 'final'
      : null;
  if (!kind) {
    return null;
  }

  const rawText = record.text;
  if (
    rawText !== undefined &&
    rawText !== null &&
    typeof rawText !== 'string'
  ) {
    return null;
  }
  if (typeof rawText === 'string' && rawText.length > MAX_TRANSCRIPT_LENGTH) {
    return null;
  }

  const rawMetrics = record.metrics;
  const metrics: Record<string, number | null> = {};
  if (rawMetrics !== undefined && rawMetrics !== null) {
    if (typeof rawMetrics !== 'object' || Array.isArray(rawMetrics)) {
      return null;
    }
    for (const [key, value] of Object.entries(rawMetrics)) {
      if (Object.keys(metrics).length >= MAX_METRIC_COUNT) {
        break;
      }
      if (typeof value !== 'number' && value !== null) {
        continue;
      }
      if (typeof value === 'number' && !Number.isFinite(value)) {
        continue;
      }
      metrics[key.slice(0, MAX_METRIC_KEY_LENGTH)] = value;
    }
  }

  const text = typeof rawText === 'string' ? rawText : '';
  if (kind === 'partial' && !text.trim()) {
    return null;
  }

  return {
    kind,
    sessionId: readString(record.sessionId ?? record.session_id, MAX_ID_LENGTH),
    turnId: readString(record.turnId ?? record.turn_id, MAX_ID_LENGTH),
    responseId: readString(
      record.responseId ?? record.response_id,
      MAX_ID_LENGTH,
    ),
    text,
    sequence: readSequence(
      record.transcriptSequence ?? record.transcript_sequence,
    ),
    timestampMs: readTimestamp(record.timestampMs ?? record.timestamp_ms),
    language: readString(record.language, 32),
    audioDurationMs: readNonNegativeNumber(
      record.audioDurationMs ?? record.audio_duration_ms,
    ),
    metrics,
  };
}

function readSequence(value: unknown): number | null {
  return typeof value === 'number' && Number.isInteger(value) && value >= 0
    ? value
    : null;
}

function readNonNegativeNumber(value: unknown): number | null {
  return typeof value === 'number' && Number.isFinite(value) && value >= 0
    ? value
    : null;
}

function safeVoiceError(error: unknown): string {
  const message =
    error instanceof Error
      ? error.message
      : typeof error === 'string'
      ? error
      : '';
  if (
    /^E_VOICE_[A-Z_]+:/.test(message) ||
    /voice|microphone|connection|session|turn/i.test(message)
  ) {
    return message.slice(0, 180);
  }
  return 'Voice connection could not be completed. Try again.';
}

function isTransportConnected(status: VoiceGatewayStatus): boolean {
  return (
    status.connected ||
    [
      'CONNECTED',
      'SESSION_STARTING',
      'SESSION_READY',
      'TURN_STARTING',
      'STREAMING_AUDIO',
    ].includes(status.state.toUpperCase())
  );
}

function isTerminalSessionError(code: string | undefined): boolean {
  return Boolean(code && TERMINAL_SESSION_ERROR_CODES.has(code.toLowerCase()));
}

function isAuthenticationExpiredError(
  value: string | null | undefined,
): boolean {
  if (!value) {
    return false;
  }
  const normalized = value.toLowerCase();
  return [...AUTH_EXPIRY_ERROR_CODES].some(code => normalized.includes(code));
}

const MAX_TRANSCRIPT_LENGTH = 16 * 1024;
const MAX_METRIC_COUNT = 32;
const MAX_METRIC_KEY_LENGTH = 64;
