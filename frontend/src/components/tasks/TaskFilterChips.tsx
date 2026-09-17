import React from 'react';
import { Pressable, StyleSheet, View } from 'react-native';
import { AppText } from '../ui/Primitives';
import { useAppTheme } from '../../design/ThemeProvider';
import { radii, spacing, typography } from '../../theme';
import { strings } from '../../i18n/strings';

export type Page = 'tasks' | 'reminders';
export type TaskFilter = 'upcoming' | 'all' | 'completed';
export type ReminderFilter = 'upcoming' | 'all' | 'sent' | 'failed';

interface TaskFilterChipsProps {
  page: Page;
  onPageChange: (page: Page) => void;
  taskFilter: TaskFilter;
  onTaskFilterChange: (filter: TaskFilter) => void;
  reminderFilter: ReminderFilter;
  onReminderFilterChange: (filter: ReminderFilter) => void;
  taskCounts?: { upcoming: number; all: number; completed: number };
  reminderCounts?: {
    upcoming: number;
    all: number;
    sent: number;
    failed: number;
  };
}

export function TaskFilterChips({
  page,
  onPageChange,
  taskFilter,
  onTaskFilterChange,
  reminderFilter,
  onReminderFilterChange,
  taskCounts,
  reminderCounts,
}: TaskFilterChipsProps) {
  const { colors } = useAppTheme();

  return (
    <View style={styles.container}>
      {/* Segmented Mode Control: Tasks vs Reminders */}
      <View
        accessibilityRole="tablist"
        style={[
          styles.segmentedControl,
          {
            backgroundColor: colors.surfaceMuted,
            borderColor: colors.borderSubtle,
          },
        ]}
      >
        <Pressable
          accessibilityLabel={strings.tasks.tasksTab}
          accessibilityRole="tab"
          accessibilityState={{ selected: page === 'tasks' }}
          onPress={() => onPageChange('tasks')}
          style={[
            styles.segmentButton,
            page === 'tasks' && [
              styles.segmentButtonActive,
              {
                backgroundColor: colors.surface,
                borderColor: colors.border,
              },
            ],
          ]}
          testID="tasks-tab"
        >
          <AppText
            style={[
              styles.segmentText,
              {
                color: page === 'tasks' ? colors.primary : colors.textMuted,
                fontWeight: page === 'tasks' ? '700' : '500',
              },
            ]}
          >
            📋 {strings.tasks.tasksTab}
            {taskCounts ? ` (${taskCounts.all})` : ''}
          </AppText>
        </Pressable>

        <Pressable
          accessibilityLabel={strings.tasks.remindersTab}
          accessibilityRole="tab"
          accessibilityState={{ selected: page === 'reminders' }}
          onPress={() => onPageChange('reminders')}
          style={[
            styles.segmentButton,
            page === 'reminders' && [
              styles.segmentButtonActive,
              {
                backgroundColor: colors.surface,
                borderColor: colors.border,
              },
            ],
          ]}
          testID="reminders-tab"
        >
          <AppText
            style={[
              styles.segmentText,
              {
                color: page === 'reminders' ? colors.primary : colors.textMuted,
                fontWeight: page === 'reminders' ? '700' : '500',
              },
            ]}
          >
            🔔 {strings.tasks.remindersTab}
            {reminderCounts ? ` (${reminderCounts.all})` : ''}
          </AppText>
        </Pressable>
      </View>

      {/* Filter Chips Bar */}
      <View accessibilityRole="tablist" style={styles.filterRow}>
        {page === 'tasks' ? (
          <>
            <FilterChip
              count={taskCounts?.upcoming}
              label={strings.tasks.upcoming}
              onPress={() => onTaskFilterChange('upcoming')}
              selected={taskFilter === 'upcoming'}
              testID="tasks-filter-upcoming"
            />
            <FilterChip
              count={taskCounts?.all}
              label={strings.tasks.all}
              onPress={() => onTaskFilterChange('all')}
              selected={taskFilter === 'all'}
              testID="tasks-filter-all"
            />
            <FilterChip
              count={taskCounts?.completed}
              label={strings.tasks.completed}
              onPress={() => onTaskFilterChange('completed')}
              selected={taskFilter === 'completed'}
              testID="tasks-filter-completed"
            />
          </>
        ) : (
          <>
            <FilterChip
              count={reminderCounts?.upcoming}
              label={strings.tasks.upcoming}
              onPress={() => onReminderFilterChange('upcoming')}
              selected={reminderFilter === 'upcoming'}
              testID="reminders-filter-upcoming"
            />
            <FilterChip
              count={reminderCounts?.all}
              label={strings.tasks.all}
              onPress={() => onReminderFilterChange('all')}
              selected={reminderFilter === 'all'}
              testID="reminders-filter-all"
            />
            <FilterChip
              count={reminderCounts?.sent}
              label={strings.tasks.sent}
              onPress={() => onReminderFilterChange('sent')}
              selected={reminderFilter === 'sent'}
              testID="reminders-filter-sent"
            />
            <FilterChip
              count={reminderCounts?.failed}
              label={reminderCounts?.failed ? `${strings.tasks.failed} (!)` : strings.tasks.failed}
              onPress={() => onReminderFilterChange('failed')}
              selected={reminderFilter === 'failed'}
              testID="reminders-filter-failed"
            />
          </>
        )}
      </View>
    </View>
  );
}

function FilterChip({
  label,
  selected,
  onPress,
  testID,
  count,
}: {
  label: string;
  selected: boolean;
  onPress: () => void;
  testID: string;
  count?: number;
}) {
  const { colors } = useAppTheme();
  return (
    <Pressable
      accessibilityRole="tab"
      accessibilityState={{ selected }}
      onPress={onPress}
      style={[
        styles.chip,
        {
          borderColor: selected ? colors.primary : colors.border,
          backgroundColor: selected ? colors.primaryContainer : colors.surface,
        },
      ]}
      testID={testID}
    >
      <AppText
        style={[
          styles.chipText,
          {
            color: selected ? colors.onPrimaryContainer : colors.textMuted,
            fontWeight: selected ? '700' : '500',
          },
        ]}
      >
        {label}
        {count !== undefined ? ` (${count})` : ''}
      </AppText>
    </Pressable>
  );
}

const styles = StyleSheet.create({
  container: {
    gap: spacing.sm,
  },
  segmentedControl: {
    borderRadius: radii.full,
    borderWidth: 1,
    flexDirection: 'row',
    padding: 3,
  },
  segmentButton: {
    alignItems: 'center',
    borderRadius: radii.full,
    flex: 1,
    justifyContent: 'center',
    minHeight: 44,
    paddingHorizontal: spacing.md,
    paddingVertical: spacing.xs,
  },
  segmentButtonActive: {
    borderWidth: 1,
  },
  segmentText: {
    fontSize: typography.body,
  },
  filterRow: {
    flexDirection: 'row',
    flexWrap: 'wrap',
    gap: spacing.xs,
  },
  chip: {
    alignItems: 'center',
    borderRadius: radii.pill,
    borderWidth: 1,
    justifyContent: 'center',
    minHeight: 40,
    paddingHorizontal: spacing.md,
    paddingVertical: 6,
  },
  chipText: {
    fontSize: typography.caption,
  },
});
