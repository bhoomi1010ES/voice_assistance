import React from 'react';
import { useCallback, useEffect, useMemo, useRef, useState } from 'react';
import {
  AccessibilityInfo,
  Alert,
  Pressable,
  ScrollView,
  ScrollViewInstance,
  StyleSheet,
  View,
} from 'react-native';
import { strings } from '../i18n/strings';
import {
  ActionButton,
  AppText,
  Card,
  Heading,
  Screen,
  StatusBanner,
} from '../components/ui/Primitives';
import { useAuth } from '../auth/AuthProvider';
import { spacing, typography } from '../design/tokens';
import { useVoiceSocket } from '../voice/VoiceSocketProvider';
import {
  VoiceConnectionState,
  VoiceSocketSnapshot,
} from '../voice/VoiceSocket';
import {
  mapTranscriptError,
  VoiceTranscriptMessage,
} from '../voice/transcript';
import {
  ConversationAssistantMessage,
  ConversationMessage,
  ConversationToolMessage,
  ConversationUserMessage,
  mapConversationError,
} from '../voice/conversation';
import {
  copyTextToClipboard,
  getVoiceOutputPreferences,
  setVoiceOutputEnabled,
} from '../native/VoiceModule';

export function AssistantScreen() {
  const { profile } = useAuth();
  const socketState = useVoiceSocket();
  const { socket } = socketState;
  const [busy, setBusy] = useState(false);
  const [copiedMessageId, setCopiedMessageId] = useState<string | null>(null);
  const [historyNoticeVisible, setHistoryNoticeVisible] = useState(false);
  const announcedFinalIds = useRef(new Set<string>());
  const scrollViewRef = useRef<ScrollViewInstance | null>(null);
  const userScrolling = useRef(false);
  const atBottom = useRef(true);
  const transcriptMessages = useMemo(
    () => socketState.transcriptMessages ?? EMPTY_TRANSCRIPT_MESSAGES,
    [socketState.transcriptMessages],
  );
  const conversationMessages = useMemo(
    () => socketState.conversationMessages ?? EMPTY_CONVERSATION_MESSAGES,
    [socketState.conversationMessages],
  );
  const pendingConfirmation = useMemo(
    () =>
      [...conversationMessages]
        .reverse()
        .find(
          (message): message is ConversationToolMessage =>
            message.role === 'tool' &&
            message.status === 'confirmation_required' &&
            Boolean(message.confirmationId),
        ) ?? null,
    [conversationMessages],
  );
  const [voiceOutputEnabled, setVoiceOutputEnabledState] = useState(true);
  const [showTtsBuffering, setShowTtsBuffering] = useState(false);
  const voiceConfirmationPending =
    socketState.confirmationAwaitingVoice || Boolean(pendingConfirmation);
  const greetingName =
    profile?.name?.trim() ||
    profile?.email?.split('@')[0] ||
    strings.assistant.defaultName;

  useEffect(() => {
    socket.connect().catch(() => undefined);
  }, [socket]);

  useEffect(() => {
    getVoiceOutputPreferences()
      .then(preferences => setVoiceOutputEnabledState(preferences.enabled))
      .catch(() => undefined);
  }, []);

  useEffect(() => {
    if (socketState.ttsPlaybackState !== 'buffering') {
      setShowTtsBuffering(false);
      return;
    }
    const timer = setTimeout(() => setShowTtsBuffering(true), 300);
    return () => clearTimeout(timer);
  }, [socketState.ttsPlaybackState]);

  const execute = useCallback(async (operation: () => Promise<void>) => {
    setBusy(true);
    try {
      await operation();
    } catch {
      // VoiceSocket publishes a safe action error in its snapshot.
    } finally {
      setBusy(false);
    }
  }, []);

  const isConnectionError =
    socketState.connection === 'failed' ||
    socketState.connection === 'degraded' ||
    Boolean(socketState.transcriptError);

  useEffect(() => {
    [
      ...conversationMessages
        .filter(
          (message): message is ConversationUserMessage =>
            message.role === 'user' && message.final && Boolean(message.text),
        )
        .map(message => ({
          id: message.id,
          text: `You said: ${message.text}`,
        })),
      ...conversationMessages
        .filter(
          (message): message is ConversationAssistantMessage =>
            message.role === 'assistant' && message.status === 'completed',
        )
        .map(message => ({
          id: message.id,
          text: `Assistant: ${message.text}`,
        })),
    ].forEach(message => {
      if (announcedFinalIds.current.has(message.id)) {
        return;
      }
      announcedFinalIds.current.add(message.id);
      AccessibilityInfo.announceForAccessibility(message.text);
    });
  }, [conversationMessages]);

  useEffect(() => {
    if (
      conversationMessages.length &&
      atBottom.current &&
      !userScrolling.current
    ) {
      scrollViewRef.current?.scrollToEnd({ animated: true });
    }
  }, [conversationMessages]);

  useEffect(() => {
    const activeResponse = [...conversationMessages]
      .reverse()
      .find(
        (message): message is ConversationAssistantMessage =>
          message.role === 'assistant' && message.firstTextAtMs !== null,
      );
    if (activeResponse && activeResponse.renderCompletedAtMs === null) {
      socket.markConversationRendered(activeResponse.responseId);
    }
  }, [conversationMessages, socket]);

  const startNewConversation = useCallback(() => {
    Alert.alert(
      strings.assistant.startNewConversationTitle,
      strings.assistant.startNewConversationBody,
      [
        { text: strings.assistant.cancel, style: 'cancel' },
        {
          text: strings.assistant.confirm,
          style: 'destructive',
          onPress: () => execute(() => socket.resetConversation()),
        },
      ],
    );
  }, [execute, socket]);

  const copyMessage = useCallback(
    (message: ConversationAssistantMessage) =>
      execute(async () => {
        await copyTextToClipboard(message.text);
        setCopiedMessageId(message.id);
      }),
    [execute],
  );

  const toggleVoiceOutput = useCallback(
    () =>
      execute(async () => {
        const preferences = await setVoiceOutputEnabled(!voiceOutputEnabled);
        setVoiceOutputEnabledState(preferences.enabled);
        if (!preferences.enabled) {
          await socket.stopPlayback();
        }
      }),
    [execute, socket, voiceOutputEnabled],
  );

  const voiceControlAction = useCallback(() => {
    if (voiceConfirmationPending) {
      return Promise.resolve();
    }
    if (socketState.connection !== 'connected') {
      return socketState.connection === 'disconnected'
        ? socket.connect()
        : socket.retry();
    }
    if (socketState.session === 'idle') return socket.startSession();
    if (socketState.turn === 'idle') return socket.startTurn();
    if (
      ['starting', 'recording', 'speech_detected'].includes(socketState.turn)
    ) {
      return socket.commitTurn();
    }
    if (['committing', 'waiting'].includes(socketState.turn)) {
      return socket.cancelTurn('user_stopped_response');
    }
    const retryable = [...conversationMessages]
      .reverse()
      .find(
        (message): message is ConversationAssistantMessage =>
          message.role === 'assistant' && message.status === 'failed',
      );
    return retryable
      ? socket.retryResponse(retryable.turnId)
      : socket.startTurn();
  }, [conversationMessages, socket, socketState, voiceConfirmationPending]);

  const handleScroll = useCallback((event: any) => {
    const { contentOffset, contentSize, layoutMeasurement } = event.nativeEvent;
    const distanceFromBottom =
      contentSize.height - (contentOffset.y + layoutMeasurement.height);
    atBottom.current = distanceFromBottom <= 48;
  }, []);

  return (
    <Screen testID="assistant-screen">
      <ScrollView
        ref={scrollViewRef}
        contentContainerStyle={styles.content}
        onContentSizeChange={() => {
          if (atBottom.current && !userScrolling.current) {
            scrollViewRef.current?.scrollToEnd({ animated: false });
          }
        }}
        onScroll={handleScroll}
        onScrollBeginDrag={() => {
          userScrolling.current = true;
        }}
        onScrollEndDrag={() => {
          userScrolling.current = false;
        }}
        scrollEventThrottle={100}
      >
        <View style={styles.topBar}>
          <Heading>{strings.assistant.title}</Heading>
          <Pressable
            accessibilityLabel={strings.assistant.conversationHistory}
            accessibilityRole="button"
            onPress={() => setHistoryNoticeVisible(value => !value)}
            testID="conversation-history"
          >
            <AppText style={styles.historyButton}>•••</AppText>
          </Pressable>
        </View>
        <AppText
          accessibilityLabel={`${strings.assistant.greeting}, ${greetingName}`}
          style={styles.greeting}
          testID="assistant-greeting"
        >
          {strings.assistant.greeting}, {greetingName}
        </AppText>
        <AppText style={styles.subtitle}>
          {connectionCopy(socketState.connection, socketState.session)}
        </AppText>
        <View testID="voice-connection-status">
          <StatusBanner tone={isConnectionError ? 'error' : 'info'}>
            {statusCopy(socketState)}
          </StatusBanner>
        </View>

        {historyNoticeVisible ? (
          <Card style={styles.historyNotice}>
            <AppText style={styles.diagnosticsTitle}>
              {strings.assistant.conversationHistory}
            </AppText>
            <AppText>{strings.assistant.localHistoryNotice}</AppText>
          </Card>
        ) : null}

        <Card style={styles.card}>
          {voiceConfirmationPending ? (
            <View testID="voice-confirmation-status">
              <AppText accessibilityLiveRegion="polite">
                {strings.assistant.voiceConfirmationListening}
              </AppText>
            </View>
          ) : (
            <VoiceOrb
              busy={busy}
              label={voiceControlLabel(socketState, conversationMessages)}
              onPress={() => execute(voiceControlAction)}
            />
          )}
          {socketState.connection === 'disconnected' ? (
            <ActionButton
              label={strings.assistant.connect}
              onPress={() => execute(() => socket.connect())}
              disabled={busy}
              testID="voice-connect"
            />
          ) : null}
          {['failed', 'degraded', 'reconnecting'].includes(
            socketState.connection,
          ) ? (
            <ActionButton
              label={strings.assistant.retry}
              onPress={() => execute(() => socket.retry())}
              disabled={busy}
              testID="voice-retry"
            />
          ) : null}
          {socketState.connection === 'connected' &&
          socketState.session === 'idle' ? (
            <ActionButton
              label={strings.assistant.startSession}
              onPress={() => execute(() => socket.startSession())}
              disabled={busy}
              testID="voice-start-session"
            />
          ) : null}
          {socketState.session === 'ready' &&
          socketState.turn === 'idle' &&
          !voiceConfirmationPending ? (
            <ActionButton
              label={strings.assistant.startTurn}
              onPress={() => execute(() => socket.startTurn())}
              disabled={busy}
              testID="voice-start-turn"
            />
          ) : null}
          {['starting', 'recording', 'speech_detected'].includes(
            socketState.turn,
          ) ? (
            <View style={styles.actionGroup}>
              <AppText style={styles.turnStatus}>
                {turnCopy(socketState.turn)}
              </AppText>
              <ActionButton
                label={strings.assistant.endTurn}
                onPress={() => execute(() => socket.commitTurn())}
                disabled={busy}
                testID="voice-finish-turn"
              />
            </View>
          ) : null}
          {['committing', 'waiting'].includes(socketState.turn) ? (
            <View style={styles.actionGroup}>
              <AppText style={styles.turnStatus}>
                {turnCopy(socketState.turn)}
              </AppText>
              <ActionButton
                label={strings.assistant.stopResponse}
                onPress={() => execute(() => socket.cancelTurn())}
                disabled={busy}
                variant="secondary"
                testID="voice-cancel-response"
              />
            </View>
          ) : null}
          {socketState.turn === 'failed' ? (
            <View style={styles.actionGroup}>
              <AppText style={styles.turnStatus}>
                {socketState.transcriptError?.message ??
                  strings.assistant.turnFailed}
              </AppText>
              {socketState.transcriptError?.retryable !== false ? (
                <ActionButton
                  label={strings.assistant.tryAgain}
                  onPress={() => execute(voiceControlAction)}
                  disabled={busy || socketState.session !== 'ready'}
                  testID="voice-retry-turn"
                />
              ) : null}
            </View>
          ) : null}
          {socketState.session !== 'idle' ? (
            <ActionButton
              label={strings.assistant.endSession}
              onPress={() => execute(() => socket.endSession())}
              disabled={busy || socketState.session === 'ending'}
              variant="quiet"
              testID="voice-end-session"
            />
          ) : null}
        </Card>

        <Card style={styles.voiceOutputCard} testID="voice-output-settings">
          <AppText style={styles.diagnosticsTitle}>
            {strings.assistant.voiceOutput}
          </AppText>
          <AppText>
            {voiceOutputEnabled
              ? strings.assistant.voiceOutputEnabled
              : strings.assistant.voiceOutputMuted}
          </AppText>
          <AppText style={styles.messageMuted}>
            {strings.assistant.serverSelectedVoice}
          </AppText>
          <ActionButton
            label={
              voiceOutputEnabled
                ? strings.assistant.muteVoiceOutput
                : strings.assistant.enableVoiceOutput
            }
            onPress={toggleVoiceOutput}
            disabled={busy}
            variant="secondary"
            testID="voice-output-toggle"
          />
        </Card>

        {socketState.ttsPlaybackState !== 'idle' &&
        socketState.ttsPlaybackState !== 'completed' ? (
          socketState.ttsPlaybackState !== 'buffering' || showTtsBuffering ? (
            <Card style={styles.voiceOutputCard} testID="tts-playback-status">
              <AppText accessibilityLiveRegion="polite">
                {ttsPlaybackCopy(socketState.ttsPlaybackState)}
              </AppText>
              {socketState.ttsPlaybackState === 'speaking' ||
              socketState.ttsPlaybackState === 'buffering' ? (
                <ActionButton
                  label={strings.assistant.stopPlayback}
                  onPress={() => execute(() => socket.stopPlayback())}
                  disabled={busy}
                  variant="secondary"
                  testID="voice-stop-playback"
                />
              ) : null}
              {socketState.ttsPlaybackState === 'failed' ? (
                <AppText style={styles.transcriptError}>
                  {socketState.ttsError ?? strings.assistant.voiceOutputFailed}
                </AppText>
              ) : null}
            </Card>
          ) : null
        ) : null}

        {conversationMessages.length ? (
          <Card style={styles.conversationCard} testID="voice-transcript">
            <AppText style={styles.diagnosticsTitle}>
              {strings.assistant.messages}
            </AppText>
            <View testID="voice-conversation">
              {conversationMessages.map(message => (
                <ConversationMessageView
                  key={message.id}
                  message={message}
                  copied={copiedMessageId === message.id}
                  onCopy={copyMessage}
                  onRetry={turnId =>
                    execute(() => socket.retryResponse(turnId))
                  }
                />
              ))}
            </View>
          </Card>
        ) : transcriptMessages.length ? (
          <Card style={styles.conversationCard} testID="voice-transcript">
            <AppText style={styles.diagnosticsTitle}>
              {strings.assistant.transcript}
            </AppText>
            {transcriptMessages.map(message => (
              <TranscriptMessageView key={message.id} message={message} />
            ))}
          </Card>
        ) : (
          <Card style={styles.emptyConversation} testID="conversation-empty">
            <AppText style={styles.emptyTitle}>
              {strings.assistant.emptyConversationTitle}
            </AppText>
            <AppText>{strings.assistant.emptyConversationBody}</AppText>
          </Card>
        )}

        {conversationMessages.length ? (
          <ActionButton
            label={strings.assistant.startNewConversation}
            onPress={startNewConversation}
            disabled={busy}
            variant="quiet"
            style={styles.resetButton}
            testID="conversation-reset"
          />
        ) : null}

        {typeof __DEV__ !== 'undefined' && __DEV__ ? (
          <Card style={styles.diagnostics} testID="voice-diagnostics">
            <AppText style={styles.diagnosticsTitle}>
              {strings.assistant.diagnostics}
            </AppText>
            <DiagnosticRow
              label={strings.assistant.event}
              value={socketState.lastEvent ?? 'NONE'}
            />
            <DiagnosticRow
              label={strings.assistant.sequence}
              value={String(socketState.eventSequence)}
            />
            <DiagnosticRow
              label={strings.assistant.heartbeat}
              value={socketState.heartbeat}
            />
            <DiagnosticRow
              label={strings.assistant.reconnectAttempt}
              value={String(socketState.reconnectAttempt)}
            />
            <DiagnosticRow
              label={strings.assistant.sessionId}
              value={shortId(socketState.sessionId)}
            />
            <DiagnosticRow
              label={strings.assistant.turnId}
              value={shortId(socketState.turnId)}
            />
            <DiagnosticRow
              label={strings.assistant.responseId}
              value={shortId(socketState.responseId)}
            />
            <DiagnosticRow
              label={strings.assistant.droppedEvents}
              value={String(socketState.droppedEventCount)}
            />
            <DiagnosticRow
              label={strings.assistant.speechToFinal}
              value={formatTranscriptTiming(transcriptMessages)}
            />
            <DiagnosticRow
              label={strings.assistant.performanceFirstText}
              value={formatMs(socketState.firstTextAtMs)}
            />
            <DiagnosticRow
              label={strings.assistant.performanceRender}
              value={formatMs(socketState.conversationRenderCompletedAtMs)}
            />
          </Card>
        ) : null}
      </ScrollView>
    </Screen>
  );
}

function VoiceOrb({
  busy,
  label,
  onPress,
}: {
  busy: boolean;
  label: string;
  onPress: () => void;
}) {
  return (
    <Pressable
      accessibilityHint="Activates the next voice session action"
      accessibilityLabel={label}
      accessibilityRole="button"
      disabled={busy}
      onPress={onPress}
      style={({ pressed }) => [
        styles.voiceOrb,
        { opacity: pressed || busy ? 0.72 : 1 },
      ]}
      testID="voice-control"
    >
      <AppText style={styles.voiceOrbIcon}>◉</AppText>
      <AppText style={styles.voiceOrbLabel}>{label}</AppText>
    </Pressable>
  );
}

function voiceControlLabel(
  snapshot: VoiceSocketSnapshot,
  messages: ConversationMessage[],
): string {
  if (snapshot.connection !== 'connected') {
    return snapshot.connection === 'disconnected'
      ? strings.assistant.connect
      : strings.assistant.retry;
  }
  if (snapshot.session === 'idle') return strings.assistant.startSession;
  if (snapshot.turn === 'idle') return strings.assistant.startTurn;
  if (['starting', 'recording', 'speech_detected'].includes(snapshot.turn)) {
    return snapshot.turn === 'speech_detected'
      ? strings.assistant.speechDetected
      : strings.assistant.listening;
  }
  if (snapshot.turn === 'committing') return strings.assistant.transcribing;
  if (snapshot.turn === 'waiting') return strings.assistant.stopResponse;
  const failed = [...messages]
    .reverse()
    .find(
      (message): message is ConversationAssistantMessage =>
        message.role === 'assistant' && message.status === 'failed',
    );
  return failed?.retryable
    ? strings.assistant.tryAgain
    : strings.assistant.startTurn;
}

function ConversationMessageView({
  copied,
  message,
  onCopy,
  onRetry,
}: {
  copied: boolean;
  message: ConversationMessage;
  onCopy: (message: ConversationAssistantMessage) => void;
  onRetry: (turnId: string) => void;
}) {
  if (message.role === 'user') {
    return <ConversationUserView message={message} />;
  }
  if (message.role === 'assistant') {
    const mapped = mapConversationError(message.errorCode);
    return (
      <View style={styles.messageRow} testID="assistant-message">
        <AppText style={styles.messageRole}>Assistant</AppText>
        {message.status === 'pending' ? (
          <AppText accessibilityLiveRegion="none" style={styles.messageMuted}>
            {strings.assistant.thinking}
          </AppText>
        ) : null}
        {message.status === 'streaming' || message.status === 'completed' ? (
          <AppText accessibilityLiveRegion="none" style={styles.messageText}>
            {message.text || strings.assistant.responding}
          </AppText>
        ) : null}
        {message.status === 'failed' ? (
          <>
            <AppText
              style={styles.transcriptError}
              testID="assistant-response-error"
            >
              {mapped.message || strings.assistant.responseFailed}
            </AppText>
            {message.retryable ? (
              <ActionButton
                label={strings.assistant.tryAgain}
                onPress={() => onRetry(message.turnId)}
                variant="secondary"
                testID="assistant-response-retry"
              />
            ) : null}
          </>
        ) : null}
        {message.status === 'cancelled' ? (
          <AppText
            style={styles.messageMuted}
            testID="assistant-response-cancelled"
          >
            {strings.assistant.responseStopped}
          </AppText>
        ) : null}
        {message.status === 'completed' && message.text ? (
          <ActionButton
            label={copied ? strings.assistant.copied : strings.assistant.copy}
            onPress={() => onCopy(message)}
            variant="quiet"
            testID="assistant-response-copy"
          />
        ) : null}
      </View>
    );
  }
  if (message.role === 'tool') {
    return <ConversationToolView message={message} />;
  }
  return (
    <View style={styles.messageRow} testID="system-message">
      <AppText style={styles.messageRole}>Status</AppText>
      <AppText
        style={message.status === 'error' ? styles.transcriptError : undefined}
      >
        {message.text}
      </AppText>
    </View>
  );
}

function ConversationUserView({
  message,
}: {
  message: ConversationUserMessage;
}) {
  const placeholder = !message.text.trim();
  return (
    <View style={styles.messageRow} testID="voice-transcript-message">
      <AppText style={styles.messageRole}>You</AppText>
      {placeholder ? (
        <AppText
          accessibilityLiveRegion="none"
          style={styles.transcriptPlaceholder}
          testID={
            message.status === 'transcribing'
              ? 'voice-transcript-transcribing'
              : message.status === 'speech_detected'
              ? 'voice-transcript-speech-detected'
              : 'voice-transcript-listening'
          }
        >
          {message.status === 'transcribing'
            ? strings.assistant.transcribing
            : message.status === 'speech_detected'
            ? strings.assistant.speechDetected
            : strings.assistant.listening}
        </AppText>
      ) : (
        <AppText
          accessibilityLabel={`You said: ${message.text}`}
          accessibilityLiveRegion="none"
          style={styles.messageText}
          testID={
            message.final
              ? 'voice-transcript-final'
              : 'voice-transcript-partial'
          }
        >
          {message.text}
        </AppText>
      )}
    </View>
  );
}

function ConversationToolView({
  message,
}: {
  message: ConversationToolMessage;
}) {
  const statusText = {
    understanding: strings.assistant.toolUnderstanding,
    confirmation_required: strings.assistant.toolConfirmationRequired,
    approved: strings.assistant.toolApproved,
    executing: strings.assistant.toolExecuting,
    success: strings.assistant.toolSuccess,
    failed: strings.assistant.toolFailed,
    cancelled: strings.assistant.toolCancelled,
  }[message.status];
  return (
    <View
      accessibilityLabel={`Assistant action: ${statusText}`}
      accessibilityLiveRegion={
        ['success', 'failed', 'cancelled'].includes(message.status)
          ? 'polite'
          : 'none'
      }
      style={styles.messageRow}
      testID="tool-status-message"
    >
      <AppText style={styles.messageRole}>Assistant action</AppText>
      <AppText testID={`tool-status-${message.status}`}>{statusText}</AppText>
    </View>
  );
}

function TranscriptMessageView({
  message,
}: {
  message: VoiceTranscriptMessage;
}) {
  if (message.status === 'partial') {
    return (
      <View testID="voice-transcript-message">
        <AppText style={styles.transcriptLabel}>
          {strings.assistant.partialTranscript}
        </AppText>
        <AppText testID="voice-transcript-partial">{message.text}</AppText>
      </View>
    );
  }

  if (message.final) {
    return (
      <View testID="voice-transcript-message">
        <AppText style={styles.transcriptLabel}>You said</AppText>
        <AppText
          accessibilityLabel={`You said: ${message.text}`}
          testID="voice-transcript-final"
        >
          {message.text}
        </AppText>
      </View>
    );
  }

  if (message.status === 'error') {
    return (
      <View testID="voice-transcript-message">
        <AppText style={styles.transcriptError} testID="voice-transcript-error">
          {mapTranscriptError(message.errorCode).message}
        </AppText>
      </View>
    );
  }

  return (
    <View testID="voice-transcript-message">
      <AppText
        accessibilityLiveRegion="none"
        style={styles.transcriptPlaceholder}
        testID={
          message.status === 'transcribing'
            ? 'voice-transcript-transcribing'
            : message.status === 'speech_detected'
            ? 'voice-transcript-speech-detected'
            : 'voice-transcript-listening'
        }
      >
        {transcriptStatusCopy(message.status)}
      </AppText>
    </View>
  );
}

function transcriptStatusCopy(
  status: VoiceTranscriptMessage['status'],
): string {
  if (status === 'transcribing') return strings.assistant.transcribing;
  if (status === 'speech_detected') return strings.assistant.speechDetected;
  return strings.assistant.listening;
}

function formatTranscriptTiming(messages: VoiceTranscriptMessage[]): string {
  const final = [...messages].reverse().find(message => message.final);
  if (!final) return 'NONE';
  const ui = final.uiSpeechEndToFinalMs;
  const server = final.serverSpeechEndToFinalMs;
  return `${formatMs(ui)} / ${formatMs(server)}`;
}

function formatMs(value: number | null): string {
  return value === null ? 'NONE' : `${Math.round(value)} ms`;
}

function connectionCopy(
  connection: VoiceConnectionState,
  session: VoiceSocketSnapshot['session'],
): string {
  if (connection === 'connecting') return strings.assistant.connecting;
  if (connection === 'reconnecting') return strings.assistant.reconnecting;
  if (connection === 'degraded') return strings.assistant.degraded;
  if (connection === 'failed') return strings.assistant.failed;
  if (connection === 'disconnected') return strings.assistant.disconnected;
  if (session === 'starting') return strings.assistant.sessionStarting;
  if (session === 'ending') return strings.assistant.sessionEnding;
  if (session === 'ready') return strings.assistant.sessionReady;
  return strings.assistant.connected;
}

function statusCopy(snapshot: VoiceSocketSnapshot): string {
  if (snapshot.error) return snapshot.error;
  return connectionCopy(snapshot.connection, snapshot.session);
}

function turnCopy(turn: VoiceSocketSnapshot['turn']): string {
  if (turn === 'starting') return strings.assistant.turnStarting;
  if (turn === 'recording') return strings.assistant.listening;
  if (turn === 'speech_detected') return strings.assistant.speechDetected;
  if (turn === 'committing') return strings.assistant.committing;
  if (turn === 'waiting') return strings.assistant.waiting;
  return strings.assistant.ready;
}

function ttsPlaybackCopy(
  state: VoiceSocketSnapshot['ttsPlaybackState'],
): string {
  if (state === 'buffering') return strings.assistant.voiceOutputBuffering;
  if (state === 'speaking') return strings.assistant.voiceOutputSpeaking;
  if (state === 'stopping') return strings.assistant.voiceOutputStopping;
  if (state === 'failed') return strings.assistant.voiceOutputFailed;
  return strings.assistant.voiceOutput;
}

function shortId(value: string | null): string {
  if (!value) return 'NONE';
  if (value.length <= 10) return value;
  return `${value.slice(0, 6)}…${value.slice(-4)}`;
}

function DiagnosticRow({ label, value }: { label: string; value: string }) {
  return (
    <View style={styles.diagnosticRow}>
      <AppText style={styles.diagnosticLabel}>{label}</AppText>
      <AppText>{value}</AppText>
    </View>
  );
}

const styles = StyleSheet.create({
  content: { paddingBottom: 24 },
  topBar: {
    alignItems: 'center',
    flexDirection: 'row',
    justifyContent: 'space-between',
  },
  historyButton: { fontSize: 28, fontWeight: '700', letterSpacing: 2 },
  historyNotice: { gap: 8, marginTop: 12 },
  greeting: {
    fontSize: typography.heading,
    fontWeight: '600',
    marginTop: spacing.sm,
  },
  subtitle: { marginBottom: 20, marginTop: 8 },
  card: { gap: 12, marginTop: 16 },
  voiceOutputCard: { gap: 12, marginTop: 16 },
  actionGroup: { gap: 12 },
  turnStatus: { marginBottom: 4 },
  voiceOrb: {
    alignItems: 'center',
    alignSelf: 'center',
    backgroundColor: '#3159D8',
    borderRadius: 72,
    height: 144,
    justifyContent: 'center',
    marginBottom: 4,
    padding: spacing.md,
    width: 144,
  },
  voiceOrbIcon: { color: '#FFFFFF', fontSize: 42, lineHeight: 46 },
  voiceOrbLabel: {
    color: '#FFFFFF',
    fontSize: typography.label,
    fontWeight: '700',
    textAlign: 'center',
  },
  conversationCard: { gap: 14, marginTop: 16 },
  emptyConversation: { gap: 8, marginTop: 16 },
  emptyTitle: { fontWeight: '700' },
  resetButton: { marginTop: 8 },
  messageRow: {
    borderBottomColor: '#E1E5EA',
    borderBottomWidth: StyleSheet.hairlineWidth,
    gap: 6,
    paddingBottom: 12,
  },
  messageRole: {
    color: '#5D6875',
    fontSize: typography.label,
    fontWeight: '700',
  },
  messageText: { lineHeight: 26 },
  messageMuted: { color: '#5D6875', fontStyle: 'italic' },
  transcriptLabel: { color: '#5D6875', fontSize: typography.label },
  transcriptPlaceholder: { color: '#5D6875' },
  transcriptError: { color: '#B42318' },
  diagnostics: { gap: 8, marginTop: 16 },
  diagnosticsTitle: { fontWeight: '700', marginBottom: 4 },
  diagnosticRow: {
    alignItems: 'center',
    flexDirection: 'row',
    justifyContent: 'space-between',
    gap: 12,
  },
  diagnosticLabel: { color: '#5D6875' },
});

const EMPTY_TRANSCRIPT_MESSAGES: VoiceTranscriptMessage[] = [];
const EMPTY_CONVERSATION_MESSAGES: ConversationMessage[] = [];
