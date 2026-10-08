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
  Text,
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
import { VoiceTranscriptMessage } from '../voice/transcript';
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
import { PlanningControls } from '../components/voice/PlanningControls';
import { PlanningReceiptCard } from '../components/voice/PlanningReceiptCard';
import { PlanDetailModal } from '../components/plans/PlanDetailModal';
import { deleteTask } from '../tasks/api';
import { PlanningActionReceipt } from '../plans/types';
import {
  ConversationBubble,
  TranscriptBubble,
} from '../components/voice/ConversationBubble';

export function AssistantScreen() {
  const { profile, controller } = useAuth();
  const socketState = useVoiceSocket();
  const { socket } = socketState;
  const { colors } = useAppTheme();
  const [busy, setBusy] = useState(false);
  const [copiedMessageId, setCopiedMessageId] = useState<string | null>(null);
  const [historyNoticeVisible, setHistoryNoticeVisible] = useState(false);
  const announcedFinalIds = useRef(new Set<string>());
  const scrollViewRef = useRef<ScrollViewInstance | null>(null);
  const userScrolling = useRef(false);
  const atBottom = useRef(false);
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

  const [selectedPlanId, setSelectedPlanId] = useState<string | null>(null);
  const [dismissedReceipt, setDismissedReceipt] = useState(false);

  useEffect(() => {
    if (socketState.recentPlanningReceipt) {
      setDismissedReceipt(false);
    }
  }, [socketState.planningReceiptVersion, socketState.recentPlanningReceipt]);

  const handleUndoAction = useCallback(
    async (action: PlanningActionReceipt) => {
      const targetId = action.targetId ?? action.id;
      if (targetId) {
        await deleteTask(controller, targetId);
      }
    },
    [controller],
  );

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
    socketState.connection === 'degraded';

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
      return socket.commitTurn({ suppressAutoListen: true });
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
          if (
            conversationMessages.length > 0 &&
            atBottom.current &&
            !userScrolling.current
          ) {
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
        scrollEnabled={historyNoticeVisible}
        scrollEventThrottle={100}
        showsHorizontalScrollIndicator={false}
        showsVerticalScrollIndicator={false}
      >
        {/* Logged in user greeting at top of home screen */}
        <View style={styles.greetingContainer}>
          <AppText
            accessibilityLabel={`${strings.assistant.greeting}, ${greetingName}`}
            style={[styles.greeting, { color: colors.text }]}
            testID="assistant-greeting"
          >
            {strings.assistant.greeting}, {greetingName} 👋
          </AppText>
        </View>

        {/* Real-time Connection Status Badge (visible when error or not connected) */}
        {isConnectionError || socketState.connection !== 'connected' ? (
          <ConnectionStatusBadge
            connection={socketState.connection}
            isError={isConnectionError}
            session={socketState.session}
            statusText={statusCopy(socketState)}
            testID="voice-connection-status"
          />
        ) : (
          <View style={styles.hiddenAccessible}>
            <ConnectionStatusBadge
              connection={socketState.connection}
              isError={isConnectionError}
              session={socketState.session}
              statusText={statusCopy(socketState)}
              testID="voice-connection-status"
            />
          </View>
        )}

        {/* Tool Confirmation Banner when voice action is pending */}
        {voiceConfirmationPending ? (
          <ToolConfirmationCard
            pendingConfirmation={pendingConfirmation}
            onStartListening={() =>
              execute(() => socket.startTurn({ autoCommitOnSpeechEnd: true }))
            }
            startListeningDisabled={
              socketState.connection !== 'connected' ||
              socketState.session !== 'ready' ||
              socketState.turn !== 'idle' ||
              socketState.ttsPlaybackState === 'speaking'
            }
            testID="voice-confirmation-status"
          />
        ) : null}

        {/* Planning Receipts */}
        <PlanningReceiptCard
          receipt={
            dismissedReceipt ? null : socketState.recentPlanningReceipt ?? null
          }
          onDismiss={() => setDismissedReceipt(true)}
          onUndoAction={handleUndoAction}
        />

        {/* Hero Interactive Voice Orb with Waveform Bars */}
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

        {/* Tap to speak / Status text below Orb */}
        <VoiceStatusView
          isActive={
            ['recording', 'speech_detected', 'committing', 'waiting'].includes(
              socketState.turn,
            ) || socketState.ttsPlaybackState === 'speaking'
          }
          primaryStatus={
            socketState.turn === 'failed'
              ? strings.assistant.tryAgain
              : ['recording', 'speech_detected'].includes(socketState.turn)
              ? strings.assistant.listening
              : ['committing', 'waiting'].includes(socketState.turn)
              ? socketState.ttsPlaybackState === 'speaking'
                ? strings.assistant.speakingStatus
                : strings.assistant.transcribing
              : strings.assistant.tapToSpeak
          }
          subStatus={
            socketState.turn === 'failed'
              ? (socketState.transcriptError?.message ??
                strings.assistant.turnFailed)
              : ['recording', 'speech_detected'].includes(socketState.turn)
              ? strings.assistant.recording
              : ['committing', 'waiting'].includes(socketState.turn)
              ? socketState.ttsPlaybackState === 'speaking'
                ? strings.assistant.interrupt
                : strings.assistant.waiting
              : strings.assistant.readyWhenYouAre
          }
        />

        {/* Normal mode / Plan mode Segmented Pill Switch */}
        <PlanningControls
          onOpenPlanDetail={planId => setSelectedPlanId(planId)}
        />

        {/* Current Chat Card: Collapsible & Expandable */}
        <View style={styles.currentChatCard}>
          {/* Header row to toggle expansion */}
          <Pressable
            accessibilityLabel={strings.assistant.currentChat}
            accessibilityRole="button"
            onPress={() => {
              setHistoryNoticeVisible(prev => {
                const next = !prev;
                if (!next) {
                  scrollViewRef.current?.scrollTo({ y: 0, animated: true });
                }
                return next;
              });
            }}
            style={styles.chatCardHeader}
          >
            <View style={styles.chatHeaderLeft}>
              <View style={styles.chatIconBadge}>
                <Text style={styles.chatIcon}>💬</Text>
              </View>
              <View style={styles.chatHeaderTitles}>
                <Text style={[styles.chatTitle, { color: colors.text }]}>
                  {strings.assistant.currentChat}
                </Text>
                <Text
                  numberOfLines={1}
                  style={[styles.chatSubtitle, { color: colors.textMuted }]}
                >
                  {strings.assistant.currentChatSubtitle}
                </Text>
              </View>
            </View>
            <View style={styles.chevronWrapper}>
              <Text style={[styles.chevronIcon, { color: colors.textMuted }]}>
                {historyNoticeVisible ? '⌃' : '⌵'}
              </Text>
            </View>
          </Pressable>

          {/* Expanded Chat Messages Body */}
          {historyNoticeVisible ? (
            <View style={styles.chatBody}>
              {conversationMessages.length ? (
                <View testID="voice-transcript">
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
                </View>
              ) : transcriptMessages.length ? (
                <View testID="voice-transcript">
                  {transcriptMessages.map(message => (
                    <TranscriptBubble key={message.id} message={message} />
                  ))}
                </View>
              ) : (
                <View>
                  {/* Sample preview matching design mockup */}
                  <View style={styles.sampleAssistantRow}>
                    <View style={styles.sampleSparkleBadge}>
                      <Text style={styles.sampleSparkleText}>✦</Text>
                    </View>
                    <View style={styles.sampleAssistantCol}>
                      <Text style={styles.sampleMetaText}>
                        Assistant • 9:40 AM
                      </Text>
                      <View style={styles.sampleAssistantBubble}>
                        <Text style={styles.sampleBubbleText}>
                          Hi! How can I help you today?
                        </Text>
                      </View>
                    </View>
                  </View>

                  <View style={styles.sampleUserRow}>
                    <View style={styles.sampleUserCol}>
                      <Text style={styles.sampleUserMetaText}>
                        You • 9:40 AM
                      </Text>
                      <View style={styles.sampleUserBubble}>
                        <Text style={styles.sampleBubbleText}>
                          Can you remind me to send the deck at 8 PM?
                        </Text>
                      </View>
                    </View>
                    <View style={styles.sampleUserBadge}>
                      <Text style={styles.sampleUserIcon}>👤</Text>
                    </View>
                  </View>

                  <View style={styles.sampleAssistantRow}>
                    <View style={styles.sampleSparkleBadge}>
                      <Text style={styles.sampleSparkleText}>✦</Text>
                    </View>
                    <View style={styles.sampleAssistantCol}>
                      <Text style={styles.sampleMetaText}>
                        Assistant • 9:40 AM
                      </Text>
                      <View style={styles.sampleAssistantBubble}>
                        <Text style={styles.sampleBubbleText}>
                          Got it! I’ll remind you to send the deck at 8 PM today.
                        </Text>
                      </View>
                    </View>
                  </View>

                  <View style={styles.sampleUserRow}>
                    <View style={styles.sampleUserCol}>
                      <Text style={styles.sampleUserMetaText}>
                        You • 9:41 AM
                      </Text>
                      <View style={styles.sampleUserBubble}>
                        <Text style={styles.sampleBubbleText}>
                          Great, thanks!
                        </Text>
                      </View>
                    </View>
                    <View style={styles.sampleUserBadge}>
                      <Text style={styles.sampleUserIcon}>👤</Text>
                    </View>
                  </View>

                  <View
                    testID="conversation-empty"
                    style={styles.hiddenAccessible}
                  />
                </View>
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
            </View>
          ) : (
            /* Preserved hidden accessible transcript nodes when collapsed */
            <View style={styles.hiddenAccessible}>
              {conversationMessages.length ? (
                <View testID="voice-transcript">
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
                </View>
              ) : (
                <View testID="conversation-empty" />
              )}
            </View>
          )}
        </View>

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
              onPress={() =>
                execute(() => socket.commitTurn({ suppressAutoListen: true }))
              }
              testID="voice-finish-turn"
            />
          </View>
        ) : null}

        {socketState.turn === 'failed' ? (
          historyNoticeVisible ? (
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
          ) : (
            <View style={styles.hiddenAccessible}>
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
          )
        ) : null}

      </ScrollView>
      <PlanDetailModal
        planId={selectedPlanId}
        visible={Boolean(selectedPlanId)}
        onClose={() => setSelectedPlanId(null)}
      />
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
  greetingContainer: {
    alignItems: 'center',
    justifyContent: 'center',
    marginTop: spacing.xs,
    marginBottom: spacing.xs,
  },
  greeting: {
    fontSize: typography.heading,
    fontWeight: '700',
    letterSpacing: -0.3,
    textAlign: 'center',
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

  hiddenAccessible: {
    height: 0,
    opacity: 0,
    overflow: 'hidden',
    width: 0,
  },
  currentChatCard: {
    backgroundColor: '#FFFFFF',
    borderColor: '#EFEAF5',
    borderRadius: radii.xl,
    borderWidth: 1,
    marginTop: spacing.md,
    padding: spacing.md,
    ...shadows.sm,
  },
  chatCardHeader: {
    alignItems: 'center',
    flexDirection: 'row',
    justifyContent: 'space-between',
    paddingVertical: 2,
  },
  chatHeaderLeft: {
    alignItems: 'center',
    flex: 1,
    flexDirection: 'row',
    gap: spacing.sm,
    marginRight: spacing.sm,
  },
  chatIconBadge: {
    alignItems: 'center',
    backgroundColor: '#F3EEFF',
    borderRadius: 20,
    height: 40,
    justifyContent: 'center',
    width: 40,
  },
  chatIcon: {
    fontSize: 18,
  },
  chatHeaderTitles: {
    flex: 1,
    gap: 2,
  },
  chatTitle: {
    fontSize: 16,
    fontWeight: '700',
  },
  chatSubtitle: {
    fontSize: 13,
  },
  chevronWrapper: {
    alignItems: 'center',
    height: 32,
    justifyContent: 'center',
    width: 32,
  },
  chevronIcon: {
    fontSize: 16,
    fontWeight: '700',
  },
  chatBody: {
    marginTop: spacing.md,
  },
  sampleAssistantRow: {
    alignItems: 'flex-start',
    flexDirection: 'row',
    gap: 8,
    marginBottom: spacing.md,
  },
  sampleSparkleBadge: {
    alignItems: 'center',
    backgroundColor: '#F3EEFF',
    borderRadius: 12,
    height: 24,
    justifyContent: 'center',
    marginTop: 4,
    width: 24,
  },
  sampleSparkleText: {
    color: '#7B61FF',
    fontSize: 14,
    fontWeight: '700',
  },
  sampleAssistantCol: {
    flex: 1,
    gap: 4,
  },
  sampleMetaText: {
    color: '#958DA5',
    fontSize: 12,
    fontWeight: '500',
  },
  sampleAssistantBubble: {
    alignSelf: 'flex-start',
    backgroundColor: '#F5F2F9',
    borderRadius: 16,
    borderTopLeftRadius: 4,
    paddingHorizontal: 14,
    paddingVertical: 10,
  },
  sampleBubbleText: {
    color: '#181725',
    fontSize: 14,
    lineHeight: 20,
  },
  sampleUserRow: {
    alignItems: 'flex-end',
    flexDirection: 'row',
    gap: 8,
    justifyContent: 'flex-end',
    marginBottom: spacing.md,
  },
  sampleUserCol: {
    alignItems: 'flex-end',
    flex: 1,
    gap: 4,
  },
  sampleUserMetaText: {
    color: '#958DA5',
    fontSize: 12,
    fontWeight: '500',
  },
  sampleUserBubble: {
    alignSelf: 'flex-end',
    backgroundColor: '#EEE8FA',
    borderRadius: 16,
    borderTopRightRadius: 4,
    paddingHorizontal: 14,
    paddingVertical: 10,
  },
  sampleUserBadge: {
    alignItems: 'center',
    backgroundColor: '#7B61FF',
    borderRadius: 14,
    height: 28,
    justifyContent: 'center',
    marginTop: 4,
    width: 28,
  },
  sampleUserIcon: {
    color: '#FFFFFF',
    fontSize: 14,
  },
});

const EMPTY_TRANSCRIPT_MESSAGES: VoiceTranscriptMessage[] = [];
const EMPTY_CONVERSATION_MESSAGES: ConversationMessage[] = [];
