import React from 'react';
import { StyleSheet, Text, View } from 'react-native';
import { useAppTheme } from '../../design/ThemeProvider';
import { radii, shadows, spacing, typography } from '../../design/tokens';
import { strings } from '../../i18n/strings';
import { ActionButton, AppText } from '../ui/Primitives';
import {
  ConversationAssistantMessage,
  ConversationMessage,
  ConversationSystemMessage,
  ConversationToolMessage,
  ConversationUserMessage,
  mapConversationError,
} from '../../voice/conversation';
import {
  mapTranscriptError,
  VoiceTranscriptMessage,
} from '../../voice/transcript';

export type ConversationBubbleProps = {
  message: ConversationMessage;
  waitPhrase?: string | null;
  copied?: boolean;
  onCopy?: (message: ConversationAssistantMessage) => void;
  onRetry?: (turnId: string) => void;
};

export function ConversationBubble({
  message,
  waitPhrase,
  copied = false,
  onCopy,
  onRetry,
}: ConversationBubbleProps) {
  if (message.role === 'user') {
    return <UserBubble message={message} />;
  }
  if (message.role === 'assistant') {
    return (
      <AssistantBubble
        copied={copied}
        message={message}
        onCopy={onCopy}
        onRetry={onRetry}
        waitPhrase={waitPhrase}
      />
    );
  }
  if (message.role === 'tool') {
    return <ToolBubble message={message} />;
  }
  return <SystemBubble message={message} />;
}

function formatMessageTime(ms?: number): string {
  if (!ms) return '9:40 AM';
  const d = new Date(ms);
  const hours = d.getHours();
  const minutes = d.getMinutes();
  const ampm = hours >= 12 ? 'PM' : 'AM';
  const formattedHours = hours % 12 || 12;
  const formattedMinutes = minutes < 10 ? `0${minutes}` : minutes;
  return `${formattedHours}:${formattedMinutes} ${ampm}`;
}

function UserBubble({ message }: { message: ConversationUserMessage }) {
  const { colors } = useAppTheme();
  const placeholder = !message.text.trim();
  const timeStr = formatMessageTime(message.startedAtMs);

  return (
    <View
      style={[styles.userRow, { alignSelf: 'flex-end' }]}
      testID="voice-transcript-message"
    >
      <View style={styles.userLabelRow}>
        <Text style={[styles.roleLabel, { color: colors.textMuted }]}>
          {`You • ${timeStr}`}
        </Text>
      </View>
      <View style={styles.userBubbleAndAvatarRow}>
        <View
          style={[
            styles.userBubble,
            {
              backgroundColor: '#EEE8FA',
              borderColor: '#E5DEF5',
            },
            shadows.sm,
          ]}
        >
          {placeholder ? (
            <AppText
              accessibilityLiveRegion="none"
              style={[styles.placeholderText, { color: colors.textMuted }]}
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
              style={[styles.userText, { color: '#181725' }]}
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
        <View style={styles.userAvatarBadge}>
          <Text style={styles.userAvatarText}>👤</Text>
        </View>
      </View>
    </View>
  );
}

function AssistantBubble({
  message,
  waitPhrase,
  copied,
  onCopy,
  onRetry,
}: {
  message: ConversationAssistantMessage;
  waitPhrase?: string | null;
  copied: boolean;
  onCopy?: (message: ConversationAssistantMessage) => void;
  onRetry?: (turnId: string) => void;
}) {
  const { colors } = useAppTheme();
  const mapped = mapConversationError(message.errorCode);
  const timeStr = formatMessageTime(message.startedAtMs);

  return (
    <View
      style={[styles.assistantRow, { alignSelf: 'flex-start' }]}
      testID="assistant-message"
    >
      {/* Header with avatar & name */}
      <View style={styles.assistantHeader}>
        <View style={styles.avatarBadge}>
          <Text style={styles.avatarText}>✦</Text>
        </View>
        <Text style={[styles.assistantName, { color: colors.textMuted }]}>
          {`Assistant • ${timeStr}`}
        </Text>
        {message.status === 'streaming' ? (
          <View
            style={[
              styles.liveBadge,
              { backgroundColor: colors.primaryContainer },
            ]}
          >
            <Text style={[styles.liveText, { color: colors.primaryDark }]}>
              Live
            </Text>
          </View>
        ) : null}
      </View>

      {/* Message Card */}
      <View
        style={[
          styles.assistantCard,
          {
            backgroundColor: '#F5F2F9',
            borderColor: '#EBE5F3',
          },
          shadows.sm,
        ]}
      >
        {message.status === 'pending' ? (
          <AppText
            accessibilityLiveRegion="none"
            style={[styles.thinkingText, { color: colors.textMuted }]}
          >
            {waitPhrase ?? strings.assistant.thinking}
          </AppText>
        ) : null}

        {message.status === 'streaming' || message.status === 'completed' ? (
          <AppText
            accessibilityLiveRegion="none"
            style={[styles.assistantText, { color: '#181725' }]}
          >
            {message.text || strings.assistant.responding}
          </AppText>
        ) : null}

        {message.status === 'failed' ? (
          <View style={styles.errorContainer}>
            <AppText
              style={[styles.errorText, { color: colors.error }]}
              testID="assistant-response-error"
            >
              {mapped.message || strings.assistant.responseFailed}
            </AppText>
            {message.retryable && onRetry ? (
              <ActionButton
                label={strings.assistant.tryAgain}
                onPress={() => onRetry(message.turnId)}
                variant="secondary"
                style={styles.retryButton}
                testID="assistant-response-retry"
              />
            ) : null}
          </View>
        ) : null}

        {message.status === 'cancelled' ? (
          <AppText
            style={[styles.cancelledText, { color: colors.textMuted }]}
            testID="assistant-response-cancelled"
          >
            {strings.assistant.responseStopped}
          </AppText>
        ) : null}

        {message.status === 'completed' && message.text && onCopy ? (
          <View style={styles.actionRow}>
            <ActionButton
              label={copied ? strings.assistant.copied : strings.assistant.copy}
              onPress={() => onCopy(message)}
              variant="quiet"
              testID="assistant-response-copy"
            />
          </View>
        ) : null}
      </View>
    </View>
  );
}

function ToolBubble({ message }: { message: ConversationToolMessage }) {
  const { colors } = useAppTheme();
  const statusText = {
    understanding: strings.assistant.toolUnderstanding,
    clarification_required: strings.assistant.toolNeedsTime,
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
      style={[
        styles.toolCard,
        {
          backgroundColor: colors.surfaceLow,
          borderColor: colors.borderSubtle,
        },
      ]}
      testID="tool-status-message"
    >
      <View style={styles.toolHeader}>
        <Text style={[styles.toolIcon, { color: colors.primary }]}>⚙</Text>
        <AppText style={[styles.toolRole, { color: colors.textMuted }]}>
          Assistant action
        </AppText>
      </View>
      <AppText
        style={[styles.toolStatus, { color: colors.text }]}
        testID={`tool-status-${message.status}`}
      >
        {statusText}
      </AppText>
    </View>
  );
}

function SystemBubble({ message }: { message: ConversationSystemMessage }) {
  const { colors } = useAppTheme();
  return (
    <View style={styles.systemRow} testID="system-message">
      <AppText style={[styles.systemRole, { color: colors.textSubtle }]}>
        Status
      </AppText>
      <AppText
        style={[
          styles.systemText,
          message.status === 'error' && { color: colors.error },
        ]}
      >
        {message.text}
      </AppText>
    </View>
  );
}

export function TranscriptBubble({
  message,
}: {
  message: VoiceTranscriptMessage;
}) {
  const { colors } = useAppTheme();

  if (message.status === 'partial') {
    return (
      <View style={styles.userRow} testID="voice-transcript-message">
        <AppText style={[styles.roleLabel, { color: colors.textSubtle }]}>
          {strings.assistant.partialTranscript}
        </AppText>
        <View
          style={[
            styles.userBubble,
            {
              backgroundColor: colors.surfaceHigh,
              borderColor: colors.borderSubtle,
            },
          ]}
        >
          <AppText
            style={[styles.userText, { color: colors.text }]}
            testID="voice-transcript-partial"
          >
            {message.text}
          </AppText>
        </View>
      </View>
    );
  }

  if (message.final) {
    return (
      <View style={styles.userRow} testID="voice-transcript-message">
        <AppText style={[styles.roleLabel, { color: colors.textSubtle }]}>
          You said
        </AppText>
        <View
          style={[
            styles.userBubble,
            {
              backgroundColor: colors.surfaceHigh,
              borderColor: colors.borderSubtle,
            },
          ]}
        >
          <AppText
            accessibilityLabel={`You said: ${message.text}`}
            style={[styles.userText, { color: colors.text }]}
            testID="voice-transcript-final"
          >
            {message.text}
          </AppText>
        </View>
      </View>
    );
  }

  if (message.status === 'error') {
    return (
      <View style={styles.userRow} testID="voice-transcript-message">
        <AppText
          style={[styles.errorText, { color: colors.error }]}
          testID="voice-transcript-error"
        >
          {mapTranscriptError(message.errorCode).message}
        </AppText>
      </View>
    );
  }

  return (
    <View style={styles.userRow} testID="voice-transcript-message">
      <AppText
        accessibilityLiveRegion="none"
        style={[styles.placeholderText, { color: colors.textMuted }]}
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
    </View>
  );
}

const styles = StyleSheet.create({
  userRow: {
    alignSelf: 'flex-end',
    marginBottom: spacing.sm,
    maxWidth: '88%',
  },
  userLabelRow: {
    alignItems: 'flex-end',
    marginBottom: 4,
    paddingRight: 40,
  },
  userBubbleAndAvatarRow: {
    alignItems: 'flex-end',
    flexDirection: 'row',
    gap: 8,
  },
  userAvatarBadge: {
    alignItems: 'center',
    backgroundColor: '#7B61FF',
    borderRadius: radii.full,
    height: 32,
    justifyContent: 'center',
    width: 32,
  },
  userAvatarText: {
    fontSize: 16,
    lineHeight: 20,
  },
  roleLabel: {
    fontSize: typography.caption,
    fontWeight: '500',
  },
  userBubble: {
    borderRadius: 18,
    borderWidth: 1,
    flexShrink: 1,
    paddingHorizontal: spacing.md,
    paddingVertical: spacing.sm,
  },
  userText: {
    fontSize: typography.body,
    lineHeight: 22,
  },
  placeholderText: {
    fontStyle: 'italic',
  },
  assistantRow: {
    marginBottom: spacing.md,
    maxWidth: '88%',
    width: '100%',
  },
  assistantHeader: {
    alignItems: 'center',
    flexDirection: 'row',
    gap: spacing.xs,
    marginBottom: 4,
  },
  avatarBadge: {
    alignItems: 'center',
    backgroundColor: '#EDE5FF',
    borderRadius: radii.full,
    height: 26,
    justifyContent: 'center',
    width: 26,
  },
  avatarText: {
    color: '#7B61FF',
    fontSize: 14,
    fontWeight: '700',
  },
  assistantName: {
    fontSize: typography.caption,
    fontWeight: '500',
  },
  liveBadge: {
    borderRadius: radii.full,
    paddingHorizontal: spacing.xs,
    paddingVertical: 1,
  },
  liveText: {
    fontSize: 10,
    fontWeight: '700',
    textTransform: 'uppercase',
  },
  assistantCard: {
    borderRadius: radii.lg,
    borderWidth: 1,
    padding: spacing.md,
    width: '100%',
  },
  assistantText: {
    fontSize: typography.body,
    lineHeight: 24,
  },
  thinkingText: {
    fontStyle: 'italic',
  },
  errorContainer: {
    gap: spacing.xs,
  },
  errorText: {
    fontSize: typography.bodySm,
    fontWeight: '500',
  },
  retryButton: {
    alignSelf: 'flex-start',
    marginTop: spacing.xs,
  },
  cancelledText: {
    fontStyle: 'italic',
  },
  actionRow: {
    alignItems: 'flex-end',
    marginTop: spacing.xs,
  },
  toolCard: {
    borderRadius: radii.md,
    borderWidth: 1,
    marginBottom: spacing.sm,
    padding: spacing.sm,
    width: '100%',
  },
  toolHeader: {
    alignItems: 'center',
    flexDirection: 'row',
    gap: spacing.xs,
    marginBottom: spacing.xxs,
  },
  toolIcon: {
    fontSize: 14,
  },
  toolRole: {
    fontSize: typography.caption,
    fontWeight: '600',
    textTransform: 'uppercase',
  },
  toolStatus: {
    fontSize: typography.bodySm,
  },
  systemRow: {
    marginBottom: spacing.sm,
    paddingHorizontal: spacing.xs,
  },
  systemRole: {
    fontSize: typography.caption,
    fontWeight: '600',
  },
  systemText: {
    fontSize: typography.bodySm,
  },
});
