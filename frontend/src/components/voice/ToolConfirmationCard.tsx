import React from 'react';
import { StyleSheet, Text, View } from 'react-native';
import { useAppTheme } from '../../design/ThemeProvider';
import { radii, shadows, spacing, typography } from '../../design/tokens';
import { strings } from '../../i18n/strings';
import { AppText, Card } from '../ui/Primitives';
import { ConversationToolMessage } from '../../voice/conversation';

export type ToolConfirmationCardProps = {
  pendingConfirmation?: ConversationToolMessage | null;
  testID?: string;
};

export function ToolConfirmationCard({
  pendingConfirmation,
  testID = 'voice-confirmation-status',
}: ToolConfirmationCardProps) {
  const { colors } = useAppTheme();

  return (
    <Card
      style={[
        styles.card,
        {
          backgroundColor: colors.surface,
          borderColor: colors.warning,
        },
        shadows.md,
      ]}
    >
      <View style={styles.headerRow}>
        <View
          style={[
            styles.iconBadge,
            { backgroundColor: colors.warningContainer },
          ]}
        >
          <Text style={[styles.iconText, { color: colors.warning }]}>⚡</Text>
        </View>
        <View style={styles.headerTextCol}>
          <Text style={[styles.badgeLabel, { color: colors.warning }]}>
            Action Pending Confirmation
          </Text>
          {pendingConfirmation?.name ? (
            <Text style={[styles.toolName, { color: colors.text }]}>
              {pendingConfirmation.name.replace(/_/g, ' ')}
            </Text>
          ) : null}
        </View>
      </View>

      <View style={styles.body} testID={testID}>
        <AppText accessibilityLiveRegion="polite" style={styles.instruction}>
          {strings.assistant.voiceConfirmationListening}
        </AppText>
        <Text style={[styles.voiceHint, { color: colors.textMuted }]}>
          Say “yes” to confirm or “no” to cancel.
        </Text>
      </View>
    </Card>
  );
}

const styles = StyleSheet.create({
  card: {
    borderRadius: radii.xl,
    borderWidth: 1.5,
    marginVertical: spacing.sm,
    padding: spacing.md,
  },
  headerRow: {
    alignItems: 'center',
    flexDirection: 'row',
    gap: spacing.sm,
    marginBottom: spacing.xs,
  },
  iconBadge: {
    alignItems: 'center',
    borderRadius: radii.full,
    height: 32,
    justifyContent: 'center',
    width: 32,
  },
  iconText: {
    fontSize: 16,
    fontWeight: '700',
  },
  headerTextCol: {
    flex: 1,
  },
  badgeLabel: {
    fontSize: typography.caption,
    fontWeight: '700',
    letterSpacing: 0.5,
    textTransform: 'uppercase',
  },
  toolName: {
    fontSize: typography.bodySm,
    fontWeight: '600',
    marginTop: 2,
    textTransform: 'capitalize',
  },
  body: {
    gap: spacing.xxs,
    marginTop: spacing.xs,
  },
  instruction: {
    fontSize: typography.body,
    fontWeight: '500',
  },
  voiceHint: {
    fontSize: typography.caption,
    fontStyle: 'italic',
  },
});
