import React from 'react';
import { StyleSheet, View } from 'react-native';
import { AppText, Card, StatusBanner } from '../ui/Primitives';
import { useAppTheme } from '../../design/ThemeProvider';
import { strings } from '../../i18n/strings';
import { Reminder } from '../../tasks/types';
import { ScheduleLine, TaskBadge, TaskButton } from './TaskPresentation';

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
  const labels = {
    scheduled: '◷ Scheduled',
    sent: '✓ Delivered',
    failed: '⊗ Failed',
    cancelled: '⊗ Cancelled',
  };
  return (
    <Card
      style={[
        styles.card,
        {
          borderColor: isFailed ? colors.error : colors.border,
        },
        reminder.status === 'cancelled' ? styles.cancelledCard : null,
      ]}
      testID={`reminder-${reminder.id}`}
    >
      <View style={styles.row}>
        <View accessible={false} style={styles.circle} />
        <View style={styles.content}>
          <View style={styles.titleRow}>
            <AppText style={styles.title}>{reminder.title}</AppText>
            <TaskBadge label={labels[reminder.status]} tone={reminder.status} />
          </View>
          {reminder.body ? (
            <AppText style={[styles.body, { color: colors.textMuted }]}>
              {reminder.body}
            </AppText>
          ) : null}
          <ScheduleLine at={reminder.trigger_at} timezone={reminder.timezone} />
          {reminder.recurrence_rule ? (
            <AppText style={[styles.recurrence, { color: colors.textMuted }]}>
              ↻ {reminder.recurrence_rule}
            </AppText>
          ) : null}
        </View>
      </View>
      {isFailed ? (
        <StatusBanner tone="error">{strings.tasks.deliveryFailed}</StatusBanner>
      ) : null}
      {isScheduled ? (
        <View style={styles.actions}>
          <TaskButton
            disabled={busy}
            label={strings.tasks.edit}
            onPress={() => onEdit(reminder)}
            testID={`reminder-edit-${reminder.id}`}
            variant="quiet"
          />
          <TaskButton
            destructive
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
  cancelledCard: { opacity: 0.75 },
  card: { borderRadius: 22, padding: 14, gap: 8, marginTop: 6 },
  row: { flexDirection: 'row', alignItems: 'flex-start', gap: 12 },
  circle: {
    width: 26,
    height: 26,
    borderRadius: 15,
    borderWidth: 2,
    borderColor: '#DCC8C7',
    marginTop: 4,
  },
  content: { flex: 1, gap: 6 },
  titleRow: {
    flexDirection: 'row',
    alignItems: 'flex-start',
    flexWrap: 'wrap',
    gap: 8,
  },
  title: {
    flexGrow: 1,
    flexShrink: 1,
    fontSize: 18,
    fontWeight: '700',
    lineHeight: 24,
    letterSpacing: -0.4,
  },
  body: { fontSize: 13, lineHeight: 20 },
  recurrence: { fontSize: 12, lineHeight: 18 },
  actions: {
    flexDirection: 'row',
    flexWrap: 'wrap',
    justifyContent: 'flex-end',
    gap: 4,
  },
});
