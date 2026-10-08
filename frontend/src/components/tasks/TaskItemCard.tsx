import React from 'react';
import { Pressable, StyleSheet, View } from 'react-native';
import { AppText, Card } from '../ui/Primitives';
import { useAppTheme } from '../../design/ThemeProvider';
import { strings } from '../../i18n/strings';
import { Task } from '../../tasks/types';
import {
  ScheduleLine,
  TaskBadge,
  TaskButton,
  presentationStyles,
} from './TaskPresentation';

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
  return (
    <Card
      style={[styles.card, isCompleted ? styles.completedCard : null]}
      testID={`todo-${task.id}`}
    >
      <View style={styles.row}>
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
          style={styles.checkTarget}
        >
          <View
            style={[
              styles.checkbox,
              isCompleted
                ? {
                    borderColor: colors.success,
                    backgroundColor: colors.success,
                  }
                : null,
            ]}
          >
            {isCompleted ? <AppText style={styles.checkmark}>✓</AppText> : null}
          </View>
        </Pressable>
        <View style={styles.content}>
          <AppText
            style={[
              styles.title,
              {
                color: isCompleted ? colors.textMuted : colors.text,
              },
              isCompleted ? styles.completedTitle : null,
            ]}
          >
            {task.title}
          </AppText>
          {task.description ? (
            <AppText style={[styles.description, { color: colors.textMuted }]}>
              {task.description}
            </AppText>
          ) : null}
          <ScheduleLine at={task.due_at} timezone={task.timezone} />
          <View style={presentationStyles.badges}>
            <TaskBadge label={`● ${task.priority}`} tone={task.priority} />
            <TaskBadge
              label={task.status.replace('_', ' ')}
              tone={
                isCompleted
                  ? 'completed'
                  : task.status === 'cancelled'
                  ? 'cancelled'
                  : 'pending'
              }
            />
            {task.source_turn_id ? (
              <TaskBadge
                label="🎙 Voice created"
                testID={`todo-voice-badge-${task.id}`}
                tone="voice"
              />
            ) : null}
          </View>
        </View>
      </View>
      <View style={styles.actions}>
        {!isCompleted ? (
          <TaskButton
            disabled={busy}
            label={strings.tasks.complete}
            onPress={() => onComplete(task)}
            style={styles.complete}
            testID={`todo-complete-${task.id}`}
          />
        ) : null}
        <TaskButton
          disabled={busy}
          label={strings.tasks.edit}
          onPress={() => onEdit(task)}
          testID={`todo-edit-${task.id}`}
          variant="quiet"
        />
        <TaskButton
          destructive
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
  completedCard: { opacity: 0.75 },
  completedTitle: { textDecorationLine: 'line-through' },
  card: { borderRadius: 22, padding: 14, gap: 12, marginTop: 6 },
  row: { flexDirection: 'row', alignItems: 'flex-start', gap: 8 },
  checkTarget: {
    width: 36,
    minHeight: 44,
    alignItems: 'center',
    paddingTop: 4,
  },
  checkbox: {
    borderColor: '#DCC8C7',
    width: 26,
    height: 26,
    borderRadius: 15,
    borderWidth: 2,
    alignItems: 'center',
    justifyContent: 'center',
  },
  checkmark: {
    color: '#FFFFFF',
    fontSize: 15,
    lineHeight: 20,
    fontWeight: '700',
  },
  content: { flex: 1, gap: 8 },
  title: {
    fontSize: 18,
    lineHeight: 24,
    fontWeight: '700',
    letterSpacing: -0.4,
  },
  description: { fontSize: 13, lineHeight: 19 },
  actions: {
    flexDirection: 'row',
    flexWrap: 'wrap',
    justifyContent: 'flex-end',
    gap: 4,
  },
  complete: { flexGrow: 1, minWidth: 105, maxWidth: 155 },
});
