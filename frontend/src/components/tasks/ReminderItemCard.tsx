import React from 'react';
import { StyleSheet, View } from 'react-native';
import { ActionButton, AppText, Card, StatusBanner } from '../ui/Primitives';
import { useAppTheme } from '../../design/ThemeProvider';
import { radii, shadows, spacing, typography } from '../../theme';
import { strings } from '../../i18n/strings';
import { formatScheduledTime } from '../../tasks/scheduling';
import { Reminder, ReminderStatus } from '../../tasks/types';

interface ReminderItemCardProps {
  reminder: Reminder;
  busy: boolean;
  onEdit: (reminder: Reminder) => void;
  onDelete: (reminder: Reminder) => void;
}

export function ReminderItemCard({
  reminder,
  busy,
  onEdit,
  onDelete,
}: ReminderItemCardProps) {
  const { colors } = useAppTheme();
  const isScheduled = reminder.status === 'scheduled';
  const isFailed = reminder.status === 'failed';
  const isSent = reminder.status === 'sent';

  const statusColors: Record<
    ReminderStatus,
    { bg: string; text: string; label: string }
  > = {
    scheduled: {
      bg: colors.secondaryContainer,
      text: colors.onSecondaryContainer,
      label: 'Scheduled',
    },
    sent: {
      bg: colors.successContainer,
      text: colors.success,
      label: 'Delivered',
    },
    failed: {
      bg: colors.errorContainer,
      text: colors.error,
      label: 'Failed',
    },
    cancelled: {
      bg: colors.surfaceMuted,
      text: colors.textMuted,
      label: 'Cancelled',
    },
  };

  const statusStyle = statusColors[reminder.status] || statusColors.scheduled;

  return (
    <Card
      style={[
        styles.card,
        {
          backgroundColor: colors.surface,
          borderColor: isFailed ? colors.error : colors.border,
          opacity: reminder.status === 'cancelled' ? 0.75 : 1,
        },
      ]}
      testID={`reminder-${reminder.id}`}
    >
      <View style={styles.headerRow}>
        <View style={styles.titleContainer}>
          <AppText style={[styles.title, { color: colors.text }]}>
            {reminder.title}
          </AppText>
          {reminder.body ? (
            <AppText style={[styles.body, { color: colors.textMuted }]}>
              {reminder.body}
            </AppText>
          ) : null}
        </View>

        <View
          style={[
            styles.badge,
            {
              backgroundColor: statusStyle.bg,
              borderColor: 'transparent',
            },
          ]}
        >
          <AppText
            style={[
              styles.badgeText,
              { color: statusStyle.text, fontWeight: '600' },
            ]}
          >
            {statusStyle.label}
          </AppText>
        </View>
      </View>

      <View style={styles.metaRow}>
        <View
          style={[
            styles.badge,
            {
              backgroundColor: colors.surfaceLow,
              borderColor: colors.borderSubtle,
            },
          ]}
        >
          <AppText style={[styles.badgeText, { color: colors.textMuted }]}>
            🔔 {formatScheduledTime(reminder.trigger_at, reminder.timezone)} ·{' '}
            {reminder.timezone}
          </AppText>
        </View>

        {reminder.recurrence_rule ? (
          <View
            style={[
              styles.badge,
              {
                backgroundColor: colors.surfaceMuted,
                borderColor: 'transparent',
              },
            ]}
          >
            <AppText style={[styles.badgeText, { color: colors.textMuted }]}>
              🔁 {reminder.recurrence_rule}
            </AppText>
          </View>
        ) : null}
      </View>

      {isFailed ? (
        <StatusBanner tone="error">{strings.tasks.deliveryFailed}</StatusBanner>
      ) : null}

      {isScheduled ? (
        <View style={styles.actionsRow}>
          <ActionButton
            disabled={busy}
            label={strings.tasks.edit}
            onPress={() => onEdit(reminder)}
            testID={`reminder-edit-${reminder.id}`}
            variant="secondary"
          />
          <ActionButton
            disabled={busy}
            label={strings.tasks.cancelReminder}
            onPress={() => onDelete(reminder)}
            testID={`reminder-delete-${reminder.id}`}
            variant="quiet"
          />
        </View>
      ) : null}
    </Card>
  );
}

const styles = StyleSheet.create({
  card: {
    borderRadius: radii.md,
    borderWidth: 1,
    gap: spacing.sm,
    marginTop: spacing.sm,
    padding: spacing.md,
    ...shadows.sm,
  },
  headerRow: {
    alignItems: 'flex-start',
    flexDirection: 'row',
    justifyContent: 'space-between',
    gap: spacing.sm,
  },
  titleContainer: {
    flex: 1,
    gap: spacing.xs,
  },
  title: {
    fontSize: typography.subheading,
    fontWeight: '600',
    letterSpacing: -0.2,
  },
  body: {
    fontSize: typography.body,
    lineHeight: 20,
  },
  metaRow: {
    flexDirection: 'row',
    flexWrap: 'wrap',
    gap: spacing.xs,
    marginTop: spacing.xs,
  },
  badge: {
    alignItems: 'center',
    borderRadius: radii.pill,
    borderWidth: 1,
    flexDirection: 'row',
    gap: 4,
    paddingHorizontal: spacing.sm,
    paddingVertical: 3,
  },
  badgeText: {
    fontSize: typography.caption,
  },
  actionsRow: {
    flexDirection: 'row',
    flexWrap: 'wrap',
    gap: spacing.sm,
    justifyContent: 'flex-end',
    marginTop: spacing.xs,
  },
});
