import React, {
  useCallback,
  useEffect,
  useMemo,
  useRef,
  useState,
} from 'react';
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
} from '../components/ui/Primitives';
import { useAuth } from '../auth/AuthProvider';
import { radii, shadows, spacing, typography } from '../design/tokens';
import { useAppTheme } from '../design/ThemeProvider';
import { useVoiceSocket } from '../voice/VoiceSocketProvider';
import {
  VoiceConnectionState,
  VoiceSocketSnapshot,
} from '../voice/VoiceSocket';
import {
  VoiceTranscriptMessage,
} from '../voice/transcript';
import {
  ConversationAssistantMessage,
  ConversationMessage,
  ConversationToolMessage,
  ConversationUserMessage,
} from '../voice/conversation';
import {
  copyTextToClipboard,
  getVoiceOutputPreferences,
  setVoiceOutputEnabled,
} from '../native/VoiceModule';
import { VoiceOrbView } from '../components/voice/VoiceOrbView';
import { ConnectionStatusBadge } from '../components/voice/ConnectionStatusBadge';
import { VoiceStatusView } from '../components/voice/VoiceStatusView';
import { QuickActionChips } from '../components/voice/QuickActionChips';
import { ToolConfirmationCard } from '../components/voice/ToolConfirmationCard';
import {
  ConversationBubble,
  TranscriptBubble,
} from '../components/voice/ConversationBubble';

export function AssistantScreen() {
  const { profile } = useAuth();
  const socketState = useVoiceSocket();
  const { socket } = socketState;
  const { colors } = useAppTheme();
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
    if (socketState.session === 'starting') return socket.startTurn();
    if (socketState.turn === 'idle') return socket.startTurn();
    if (
      ['starting', 'recording', 'speech_detected'].includes(socketState.turn)
    ) {
      return socket.commitTurn();
    }
    if (['committing', 'waiting'].includes(socketState.turn)) {
      if (
        socketState.ttsPlaybackState !== 'speaking' &&
        !socketState.followUpQueued
      ) {
        return socket.startTurn({
          preserveMicrophone: true,
          autoCommitOnSpeechEnd: true,
        });
      }
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
        showsVerticalScrollIndicator={false}
      >
        {/* Top bar with screen title & history toggle */}
        <View style={styles.topBar}>
          <Heading>{strings.assistant.title}</Heading>
          <Pressable
            accessibilityLabel={strings.assistant.conversationHistory}
            accessibilityRole="button"
            hitSlop={spacing.xs}
            onPress={() => setHistoryNoticeVisible(value => !value)}
            testID="conversation-history"
          >
            <AppText style={[styles.historyButton, { color: colors.primary }]}>
              •••
            </AppText>
          </Pressable>
        </View>

        {/* Personalized greeting & dynamic subtitle */}
        <AppText
          accessibilityLabel={`${strings.assistant.greeting}, ${greetingName}`}
          style={styles.greeting}
          testID="assistant-greeting"
        >
          {strings.assistant.greeting}, {greetingName}
        </AppText>
        <AppText style={[styles.subtitle, { color: colors.textMuted }]}>
          {connectionCopy(socketState.connection, socketState.session)}
        </AppText>

        {/* Real-time Connection Status Badge */}
        <ConnectionStatusBadge
          connection={socketState.connection}
          isError={isConnectionError}
          session={socketState.session}
          statusText={statusCopy(socketState)}
          testID="voice-connection-status"
        />

        {/* Local conversation history notice */}
        {historyNoticeVisible ? (
          <Card style={styles.historyNotice}>
            <AppText style={styles.diagnosticsTitle}>
              {strings.assistant.conversationHistory}
            </AppText>
            <AppText style={{ color: colors.textMuted }}>
              {strings.assistant.localHistoryNotice}
            </AppText>
          </Card>
        ) : null}

        {/* Primary Interactive Voice Orb Card */}
        <Card
          style={[
            styles.card,
            {
              backgroundColor: colors.surface,
              borderColor: colors.borderSubtle,
            },
            shadows.sm,
          ]}
        >
          {voiceConfirmationPending ? (
            <ToolConfirmationCard
              pendingConfirmation={pendingConfirmation}
              testID="voice-confirmation-status"
            />
          ) : (
            <>
              <VoiceOrbView
                accessibilityHint="Activates the next voice session action"
                accessibilityLabel={voiceControlLabel(
                  socketState,
                  conversationMessages,
                )}
                busy={busy}
                connectionState={socketState.connection}
                label={voiceControlLabel(socketState, conversationMessages)}
                onPress={() => execute(voiceControlAction)}
                playbackState={socketState.ttsPlaybackState}
                testID="voice-control"
                turnState={socketState.turn}
              />
              <VoiceStatusView
                isActive={
                  ['recording', 'speech_detected', 'committing', 'waiting'].includes(
                    socketState.turn,
                  ) || socketState.ttsPlaybackState === 'speaking'
                }
                primaryStatus={voiceControlLabel(
                  socketState,
                  conversationMessages,
                )}
                subStatus={turnCopy(socketState.turn)}
              />
            </>
          )}

          {/* Fallback secondary control buttons with preserved testIDs */}
          {socketState.connection === 'disconnected' ? (
            <ActionButton
              disabled={busy}
              label={strings.assistant.connect}
              onPress={() => execute(() => socket.connect())}
              testID="voice-connect"
            />
          ) : null}

          {['failed', 'degraded', 'reconnecting'].includes(
            socketState.connection,
          ) ? (
            <ActionButton
              disabled={busy}
              label={strings.assistant.retry}
              onPress={() => execute(() => socket.retry())}
              testID="voice-retry"
            />
          ) : null}

          {['starting', 'recording', 'speech_detected'].includes(
            socketState.turn,
          ) ? (
            <View style={styles.actionGroup}>
              <ActionButton
                disabled={busy}
                label={strings.assistant.endTurn}
                onPress={() => execute(() => socket.commitTurn())}
                testID="voice-finish-turn"
              />
            </View>
          ) : null}

          {socketState.turn === 'failed' ? (
            <View style={styles.actionGroup}>
              <AppText style={[styles.turnStatus, { color: colors.error }]}>
                {socketState.transcriptError?.message ??
                  strings.assistant.turnFailed}
              </AppText>
              {socketState.transcriptError?.retryable !== false ? (
                <ActionButton
                  disabled={busy || socketState.session !== 'ready'}
                  label={strings.assistant.tryAgain}
                  onPress={() => execute(voiceControlAction)}
                  testID="voice-retry-turn"
                />
              ) : null}
            </View>
          ) : null}
        </Card>

        {/* Quick example prompt suggestion chips (triggers existing voice flow, no typed prompts) */}
        {!conversationMessages.length &&
        !voiceConfirmationPending &&
        socketState.turn === 'idle' ? (
          <QuickActionChips
            onChipPress={() => execute(voiceControlAction)}
          />
        ) : null}

        {/* Voice output preferences card */}
        <Card style={styles.voiceOutputCard} testID="voice-output-settings">
          <AppText style={styles.diagnosticsTitle}>
            {strings.assistant.voiceOutput}
          </AppText>
          <AppText>
            {voiceOutputEnabled
              ? strings.assistant.voiceOutputEnabled
              : strings.assistant.voiceOutputMuted}
          </AppText>
          <AppText style={[styles.messageMuted, { color: colors.textSubtle }]}>
            {strings.assistant.serverSelectedVoice}
          </AppText>
          <ActionButton
            disabled={busy}
            label={
              voiceOutputEnabled
                ? strings.assistant.muteVoiceOutput
                : strings.assistant.enableVoiceOutput
            }
            onPress={toggleVoiceOutput}
            testID="voice-output-toggle"
            variant="secondary"
          />
        </Card>

        {/* TTS playback status banner/card */}
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
                  disabled={busy}
                  label={strings.assistant.stopPlayback}
                  onPress={() => execute(() => socket.stopPlayback())}
                  testID="voice-stop-playback"
                  variant="secondary"
                />
              ) : null}
              {socketState.ttsPlaybackState === 'failed' ? (
                <AppText style={[styles.transcriptError, { color: colors.error }]}>
                  {socketState.ttsError ?? strings.assistant.voiceOutputFailed}
                </AppText>
              ) : null}
            </Card>
          ) : null
        ) : null}

        {/* Conversation transcript card & bubbles */}
        {conversationMessages.length ? (
          <Card style={styles.conversationCard} testID="voice-transcript">
            <AppText style={styles.diagnosticsTitle}>
              {strings.assistant.messages}
            </AppText>
            <View testID="voice-conversation">
              {conversationMessages.map(message => (
                <ConversationBubble
                  key={message.id}
                  copied={copiedMessageId === message.id}
                  message={message}
                  onCopy={copyMessage}
                  onRetry={turnId =>
                    execute(() => socket.retryResponse(turnId))
                  }
                  waitPhrase={socketState.waitPhrase}
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
              <TranscriptBubble key={message.id} message={message} />
            ))}
          </Card>
        ) : (
          <Card style={styles.emptyConversation} testID="conversation-empty">
            <AppText style={styles.emptyTitle}>
              {strings.assistant.emptyConversationTitle}
            </AppText>
            <AppText style={{ color: colors.textMuted }}>
              {strings.assistant.emptyConversationBody}
            </AppText>
          </Card>
        )}

        {/* Reset conversation button */}
        {conversationMessages.length ? (
          <ActionButton
            disabled={busy}
            label={strings.assistant.startNewConversation}
            onPress={startNewConversation}
            style={styles.resetButton}
            testID="conversation-reset"
            variant="quiet"
          />
        ) : null}

        {/* Developer Diagnostics (Development mode only) */}
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
  if (snapshot.session === 'starting') return strings.assistant.startingSession;
  if (snapshot.session === 'ending') return strings.assistant.sessionEnding;
  if (snapshot.turn === 'idle') return strings.assistant.startTurn;
  if (['starting', 'recording', 'speech_detected'].includes(snapshot.turn)) {
    return snapshot.turn === 'speech_detected'
      ? strings.assistant.speechDetected
      : strings.assistant.listening;
  }
  if (snapshot.turn === 'committing') return strings.assistant.transcribing;
  if (snapshot.turn === 'waiting') {
    return snapshot.ttsPlaybackState === 'speaking'
      ? strings.assistant.interrupt
      : strings.assistant.speakNow;
  }
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
  if (snapshot.followUpQueued) return strings.assistant.followUpQueued;
  if (snapshot.ttsPlaybackState === 'speaking') {
    return strings.assistant.speakingStatus;
  }
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
  const { colors } = useAppTheme();
  return (
    <View style={styles.diagnosticRow}>
      <AppText style={[styles.diagnosticLabel, { color: colors.textMuted }]}>
        {label}
      </AppText>
      <AppText>{value}</AppText>
    </View>
  );
}

const styles = StyleSheet.create({
  content: {
    paddingBottom: spacing.xl,
    paddingHorizontal: spacing.md,
  },
  topBar: {
    alignItems: 'center',
    flexDirection: 'row',
    justifyContent: 'space-between',
    minHeight: 48,
  },
  historyButton: {
    fontSize: 24,
    fontWeight: '700',
    letterSpacing: 2,
    padding: spacing.xs,
  },
  historyNotice: {
    gap: spacing.xs,
    marginTop: spacing.sm,
  },
  greeting: {
    fontSize: typography.heading,
    fontWeight: '700',
    marginTop: spacing.xs,
  },
  subtitle: {
    fontSize: typography.bodySm,
    marginBottom: spacing.xs,
    marginTop: 2,
  },
  card: {
    borderRadius: radii.xl,
    borderWidth: 1,
    gap: spacing.sm,
    marginTop: spacing.sm,
    padding: spacing.md,
  },
  voiceOutputCard: {
    borderRadius: radii.lg,
    borderWidth: 1,
    gap: spacing.sm,
    marginTop: spacing.sm,
    padding: spacing.md,
  },
  actionGroup: {
    gap: spacing.sm,
  },
  turnStatus: {
    fontSize: typography.bodySm,
    marginBottom: 4,
  },
  conversationCard: {
    borderRadius: radii.xl,
    borderWidth: 1,
    gap: spacing.md,
    marginTop: spacing.md,
    padding: spacing.md,
  },
  emptyConversation: {
    borderRadius: radii.lg,
    borderWidth: 1,
    gap: spacing.xs,
    marginTop: spacing.md,
    padding: spacing.md,
  },
  emptyTitle: {
    fontSize: typography.body,
    fontWeight: '700',
  },
  resetButton: {
    marginTop: spacing.sm,
  },
  messageMuted: {
    fontStyle: 'italic',
  },
  transcriptError: {
    fontSize: typography.bodySm,
    fontWeight: '600',
  },
  diagnostics: {
    borderRadius: radii.lg,
    borderWidth: 1,
    gap: spacing.xs,
    marginTop: spacing.md,
    padding: spacing.md,
  },
  diagnosticsTitle: {
    fontSize: typography.body,
    fontWeight: '700',
    marginBottom: spacing.xxs,
  },
  diagnosticRow: {
    alignItems: 'center',
    flexDirection: 'row',
    gap: spacing.md,
    justifyContent: 'space-between',
    paddingVertical: 2,
  },
  diagnosticLabel: {
    fontSize: typography.caption,
  },
});

const EMPTY_TRANSCRIPT_MESSAGES: VoiceTranscriptMessage[] = [];
const EMPTY_CONVERSATION_MESSAGES: ConversationMessage[] = [];
