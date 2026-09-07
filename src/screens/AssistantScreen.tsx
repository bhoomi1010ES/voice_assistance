import React from 'react';
import { useCallback, useEffect, useMemo, useState } from 'react';
import { AccessibilityInfo, ScrollView, StyleSheet, View } from 'react-native';
import { useRef } from 'react';
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

export function AssistantScreen() {
  const { profile } = useAuth();
  const socketState = useVoiceSocket();
  const { socket } = socketState;
  const [busy, setBusy] = useState(false);
  const announcedFinalIds = useRef(new Set<string>());
  const transcriptMessages = useMemo(
    () => socketState.transcriptMessages ?? EMPTY_TRANSCRIPT_MESSAGES,
    [socketState.transcriptMessages],
  );
  const greetingName =
    profile?.name?.trim() ||
    profile?.email?.split('@')[0] ||
    strings.assistant.defaultName;

  useEffect(() => {
    socket.connect().catch(() => undefined);
  }, [socket]);

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
    transcriptMessages
      .filter(message => message.final && message.text)
      .forEach(message => {
        if (announcedFinalIds.current.has(message.id)) {
          return;
        }
        announcedFinalIds.current.add(message.id);
        AccessibilityInfo.announceForAccessibility(`You said: ${message.text}`);
      });
  }, [transcriptMessages]);

  return (
    <Screen testID="assistant-screen">
      <ScrollView contentContainerStyle={styles.content}>
        <Heading>{strings.assistant.title}</Heading>
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

        <Card style={styles.card}>
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
          {socketState.session === 'ready' && socketState.turn === 'idle' ? (
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
              {socketState.responseId ? (
                <ActionButton
                  label={strings.assistant.cancelTurn}
                  onPress={() => execute(() => socket.cancelTurn())}
                  disabled={busy}
                  variant="secondary"
                  testID="voice-cancel-turn"
                />
              ) : null}
            </View>
          ) : null}
          {['committing', 'waiting'].includes(socketState.turn) ? (
            <View style={styles.actionGroup}>
              <AppText style={styles.turnStatus}>
                {turnCopy(socketState.turn)}
              </AppText>
              {socketState.responseId ? (
                <ActionButton
                  label={strings.assistant.cancelTurn}
                  onPress={() => execute(() => socket.cancelTurn())}
                  disabled={busy}
                  variant="secondary"
                  testID="voice-cancel-response"
                />
              ) : null}
            </View>
          ) : null}
          {socketState.turn === 'failed' ? (
            <View style={styles.actionGroup}>
              <AppText style={styles.turnStatus}>
                {strings.assistant.turnFailed}
              </AppText>
              {socketState.transcriptError?.retryable !== false ? (
                <ActionButton
                  label={strings.assistant.tryAgain}
                  onPress={() => execute(() => socket.startTurn())}
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

        {transcriptMessages.length ? (
          <Card style={styles.transcriptCard} testID="voice-transcript">
            <AppText style={styles.diagnosticsTitle}>
              {strings.assistant.transcript}
            </AppText>
            {transcriptMessages.map(message => (
              <TranscriptMessageView key={message.id} message={message} />
            ))}
          </Card>
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
          </Card>
        ) : null}
      </ScrollView>
    </Screen>
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
  greeting: {
    fontSize: typography.heading,
    fontWeight: '600',
    marginTop: spacing.sm,
  },
  subtitle: { marginBottom: 20, marginTop: 8 },
  card: { marginTop: 16 },
  actionGroup: { gap: 12 },
  turnStatus: { marginBottom: 4 },
  transcriptCard: { gap: 12, marginTop: 16 },
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
