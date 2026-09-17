import React from 'react';
import { Pressable, StyleSheet, View } from 'react-native';
import { ActionButton, AppText, Card } from '../ui/Primitives';
import { useAppTheme } from '../../design/ThemeProvider';
import { radii, shadows, spacing, typography } from '../../theme';
import { strings } from '../../i18n/strings';
import { formatScheduledTime } from '../../tasks/scheduling';
import { Task, TaskPriority } from '../../tasks/types';

interface TaskItemCardProps {
  task: Task;
  busy: boolean;
  onEdit: (task: Task) => void;
  onComplete: (task: Task) => void;
  onDelete: (task: Task) => void;
}

export function TaskItemCard({
  task,
  busy,
  onEdit,
  onComplete,
  onDelete,
}: TaskItemCardProps) {
  const { colors } = useAppTheme();
  const isCompleted = task.status === 'completed';

  const priorityColors: Record<
    TaskPriority,
    { bg: string; text: string; dot: string }
  > = {
    urgent: {
      bg: colors.errorContainer,
      text: colors.error,
      dot: colors.error,
    },
    high: {
      bg: colors.primaryContainer,
      text: colors.primary,
      dot: colors.primary,
    },
    normal: {
      bg: colors.surfaceMuted,
      text: colors.textMuted,
      dot: colors.tertiary,
    },
    low: {
      bg: colors.surfaceLow,
      text: colors.textSubtle,
      dot: colors.disabled,
    },
  };

  const priorityStyle = priorityColors[task.priority] || priorityColors.normal;

  return (
    <Card
      style={[
        styles.card,
        {
          backgroundColor: colors.surface,
          borderColor: isCompleted ? colors.borderSubtle : colors.border,
          opacity: isCompleted ? 0.75 : 1,
        },
      ]}
      testID={`todo-${task.id}`}
    >
      <View style={styles.headerRow}>
        <Pressable
          accessibilityLabel={
            isCompleted
              ? `${task.title} - Completed`
              : `Mark ${task.title} as completed`
          }
          accessibilityRole="checkbox"
          accessibilityState={{ checked: isCompleted }}
          disabled={busy || isCompleted}
          onPress={() => onComplete(task)}
          style={[
            styles.checkbox,
            {
              borderColor: isCompleted ? colors.success : colors.border,
              backgroundColor: isCompleted ? colors.success : 'transparent',
            },
          ]}
        >
          {isCompleted ? (
            <AppText style={[styles.checkmark, { color: colors.onPrimary }]}>
              ✓
            </AppText>
          ) : null}
        </Pressable>

        <View style={styles.titleContainer}>
          <AppText
            style={[
              styles.title,
              {
                color: isCompleted ? colors.textMuted : colors.text,
                textDecorationLine: isCompleted ? 'line-through' : 'none',
              },
            ]}
          >
            {task.title}
          </AppText>

          {task.description ? (
            <AppText
              style={[
                styles.description,
                {
                  color: isCompleted ? colors.textSubtle : colors.textMuted,
                },
              ]}
            >
              {task.description}
            </AppText>
          ) : null}
        </View>
      </View>

      <View style={styles.metaRow}>
        {/* Schedule Badge */}
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
            🕒 {formatScheduledTime(task.due_at, task.timezone)} ·{' '}
            {task.timezone}
          </AppText>
        </View>

        {/* Priority Badge */}
        <View
          style={[
            styles.badge,
            {
              backgroundColor: priorityStyle.bg,
              borderColor: 'transparent',
            },
          ]}
        >
          <View
            style={[styles.priorityDot, { backgroundColor: priorityStyle.dot }]}
          />
          <AppText
            style={[
              styles.badgeText,
              {
                color: priorityStyle.text,
                textTransform: 'capitalize',
                fontWeight: '600',
              },
            ]}
          >
            {task.priority}
          </AppText>
        </View>

        {/* Status Badge */}
        <View
          style={[
            styles.badge,
            {
              backgroundColor: isCompleted
                ? colors.successContainer
                : colors.surfaceMuted,
              borderColor: 'transparent',
            },
          ]}
        >
          <AppText
            style={[
              styles.badgeText,
              {
                color: isCompleted ? colors.success : colors.textMuted,
                textTransform: 'capitalize',
              },
            ]}
          >
            {task.status}
          </AppText>
        </View>

        {/* Real Voice Origin Badge (Only if source_turn_id is present) */}
        {task.source_turn_id ? (
          <View
            style={[
              styles.badge,
              {
                backgroundColor: colors.secondaryContainer,
                borderColor: 'transparent',
              },
            ]}
            testID={`todo-voice-badge-${task.id}`}
          >
            <AppText
              style={[
                styles.badgeText,
                { color: colors.onSecondaryContainer, fontWeight: '600' },
              ]}
            >
              🎙 Voice Created
            </AppText>
          </View>
        ) : null}
      </View>

      <View style={styles.actionsRow}>
        {!isCompleted ? (
          <ActionButton
            disabled={busy}
            label={strings.tasks.complete}
            onPress={() => onComplete(task)}
            testID={`todo-complete-${task.id}`}
            variant="secondary"
          />
        ) : null}
        <ActionButton
          disabled={busy}
          label={strings.tasks.edit}
          onPress={() => onEdit(task)}
          testID={`todo-edit-${task.id}`}
          variant="quiet"
        />
        <ActionButton
          disabled={busy}
          label={strings.tasks.delete}
          onPress={() => onDelete(task)}
          testID={`todo-delete-${task.id}`}
          variant="quiet"
        />
      </View>
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
    gap: spacing.sm,
  },
  checkbox: {
    alignItems: 'center',
    borderRadius: radii.full,
    borderWidth: 2,
    height: 24,
    justifyContent: 'center',
    marginTop: 2,
    width: 24,
  },
  checkmark: {
    fontSize: 14,
    fontWeight: '700',
    lineHeight: 16,
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
  description: {
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
  priorityDot: {
    borderRadius: radii.full,
    height: 6,
    width: 6,
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
