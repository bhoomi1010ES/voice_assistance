import React, {
  useCallback,
  useEffect,
  useMemo,
  useRef,
  useState,
} from 'react';
import {
  Linking,
  PermissionsAndroid,
  Platform,
  RefreshControl,
  ScrollView,
  StyleSheet,
  View,
} from 'react-native';
import { useAuth } from '../auth/AuthProvider';
import { safeUserMessage, toClientError } from '../api/errors';
import {
  ActionButton,
  AppText,
  Heading,
  Screen,
  StatusBanner,
} from '../components/ui/Primitives';
import { useAppTheme } from '../design/ThemeProvider';
import { radii, spacing, typography } from '../theme';
import { strings } from '../i18n/strings';
import {
  completeTask,
  createReminder,
  createTask,
  deleteReminder,
  deleteTask,
  listReminders,
  listTasks,
  updateReminder,
  updateTask,
} from '../tasks/api';
import {
  buildLocalDateTime,
  dateInputForOffset,
  deviceTimezone,
  localInputParts,
  recurrenceChoice,
  recurrenceRule,
} from '../tasks/scheduling';
import { Reminder, Task } from '../tasks/types';

import { TaskItemCard } from '../components/tasks/TaskItemCard';
import { ReminderItemCard } from '../components/tasks/ReminderItemCard';
import {
  Page,
  ReminderFilter,
  TaskFilter,
  TaskFilterChips,
} from '../components/tasks/TaskFilterChips';
import { TaskDraft, TaskEditorModal } from '../components/tasks/TaskEditorModal';
import {
  ReminderDraft,
  ReminderEditorModal,
} from '../components/tasks/ReminderEditorModal';
import { TaskEmptyState } from '../components/tasks/TaskEmptyState';
import { PushPermissionCard } from '../components/tasks/PushPermissionCard';
import { Confirmation, ConfirmModal } from '../components/tasks/ConfirmModal';

const PHASE7_RECURRENCE_ACCEPTED = true;

const EMPTY_TASK_DRAFT: TaskDraft = {
  title: '',
  description: '',
  date: '',
  time: '',
  timezone: deviceTimezone(),
  priority: 'normal',
};

const EMPTY_REMINDER_DRAFT: ReminderDraft = {
  title: '',
  body: '',
  date: '',
  time: '',
  timezone: deviceTimezone(),
  recurrence: 'none',
};

export function TasksScreen() {
  const { controller, profile } = useAuth();
  const { colors } = useAppTheme();

  const [page, setPage] = useState<Page>('tasks');
  const [taskFilter, setTaskFilter] = useState<TaskFilter>('upcoming');
  const [reminderFilter, setReminderFilter] =
    useState<ReminderFilter>('upcoming');
  const [tasks, setTasks] = useState<Task[]>([]);
  const [reminders, setReminders] = useState<Reminder[]>([]);
  const [loading, setLoading] = useState(true);
  const [refreshing, setRefreshing] = useState(false);
  const [busyKey, setBusyKey] = useState<string | null>(null);
  const [notice, setNotice] = useState<{
    text: string;
    error?: boolean;
  } | null>(null);

  const [taskForm, setTaskForm] = useState<TaskDraft>(EMPTY_TASK_DRAFT);
  const [reminderForm, setReminderForm] =
    useState<ReminderDraft>(EMPTY_REMINDER_DRAFT);
  const [taskFormMode, setTaskFormMode] = useState<'create' | 'edit'>('create');
  const [reminderFormMode, setReminderFormMode] = useState<'create' | 'edit'>('create');
  const [editingTaskId, setEditingTaskId] = useState<string | null>(null);
  const [editingReminderId, setEditingReminderId] = useState<string | null>(null);
  const [taskFormOpen, setTaskFormOpen] = useState(false);
  const [reminderFormOpen, setReminderFormOpen] = useState(false);
  const [confirmation, setConfirmation] = useState<Confirmation | null>(null);
  const [pushState, setPushState] = useState<'unknown' | 'granted' | 'denied'>('unknown');

  const actionKeys = useRef(new Set<string>());
  const userTimezone = profile?.timezone || deviceTimezone();

  const runOnce = useCallback(
    async (key: string, operation: () => Promise<void>) => {
      if (actionKeys.current.has(key)) return;
      actionKeys.current.add(key);
      setBusyKey(key);
      setNotice(null);
      try {
        await operation();
      } catch (error) {
        const clientError = toClientError(error);
        setNotice({
          text:
            clientError.code === 'TEMPORAL_RESOLUTION_REQUIRED'
              ? 'The server needs a clearer date, time, or timezone. Review the preview and try again.'
              : safeUserMessage(clientError),
          error: true,
        });
      } finally {
        actionKeys.current.delete(key);
        setBusyKey(current => (current === key ? null : current));
      }
    },
    [],
  );

  const load = useCallback(
    async (refresh = false) => {
      if (refresh) setRefreshing(true);
      else setLoading(true);
      try {
        const [nextTasks, nextReminders] = await Promise.all([
          listTasks(controller),
          listReminders(controller),
        ]);
        setTasks(nextTasks);
        setReminders(nextReminders);
        setNotice(null);
      } catch (error) {
        setNotice({ text: safeUserMessage(toClientError(error)), error: true });
      } finally {
        setLoading(false);
        setRefreshing(false);
      }
    },
    [controller],
  );

  useEffect(() => {
    load().catch(() => undefined);
  }, [load]);

  const visibleTasks = useMemo(() => {
    if (taskFilter === 'completed') {
      return tasks.filter(task => task.status === 'completed');
    }
    if (taskFilter === 'upcoming') {
      return tasks.filter(
        task => task.status !== 'completed' && task.status !== 'cancelled',
      );
    }
    return tasks;
  }, [taskFilter, tasks]);

  const visibleReminders = useMemo(() => {
    if (reminderFilter === 'upcoming') {
      return reminders.filter(reminder => reminder.status === 'scheduled');
    }
    if (reminderFilter === 'all') return reminders;
    return reminders.filter(reminder => reminder.status === reminderFilter);
  }, [reminderFilter, reminders]);

  const taskCounts = useMemo(() => {
    const upcoming = tasks.filter(
      t => t.status !== 'completed' && t.status !== 'cancelled',
    ).length;
    const completed = tasks.filter(t => t.status === 'completed').length;
    return { upcoming, all: tasks.length, completed };
  }, [tasks]);

  const reminderCounts = useMemo(() => {
    const upcoming = reminders.filter(r => r.status === 'scheduled').length;
    const sent = reminders.filter(r => r.status === 'sent').length;
    const failed = reminders.filter(r => r.status === 'failed').length;
    return { upcoming, all: reminders.length, sent, failed };
  }, [reminders]);

  const openCreateTask = () => {
    setTaskForm({ ...EMPTY_TASK_DRAFT, timezone: userTimezone });
    setEditingTaskId(null);
    setTaskFormMode('create');
    setTaskFormOpen(true);
  };

  const openEditTask = (task: Task) => {
    const parts = localInputParts(task.due_at, task.timezone);
    setTaskForm({
      title: task.title,
      description: task.description ?? '',
      date: parts.date,
      time: parts.time,
      timezone: task.timezone,
      priority: task.priority,
    });
    setEditingTaskId(task.id);
    setTaskFormMode('edit');
    setTaskFormOpen(true);
  };

  const openCreateReminder = () => {
    setReminderForm({ ...EMPTY_REMINDER_DRAFT, timezone: userTimezone });
    setEditingReminderId(null);
    setReminderFormMode('create');
    setReminderFormOpen(true);
  };

  const openEditReminder = (reminder: Reminder) => {
    const parts = localInputParts(reminder.trigger_at, reminder.timezone);
    setReminderForm({
      title: reminder.title,
      body: reminder.body ?? '',
      date: parts.date,
      time: parts.time,
      timezone: reminder.timezone,
      recurrence: recurrenceChoice(reminder.recurrence_rule),
    });
    setEditingReminderId(reminder.id);
    setReminderFormMode('edit');
    setReminderFormOpen(true);
  };

  const saveTask = async () => {
    const dueAt =
      taskForm.date || taskForm.time
        ? buildLocalDateTime(taskForm.date, taskForm.time)
        : null;
    if (
      !taskForm.title.trim() ||
      ((taskForm.date || taskForm.time) && !dueAt)
    ) {
      setNotice({
        text: 'Enter a title and a complete valid date/time.',
        error: true,
      });
      return;
    }
    const input = {
      title: taskForm.title.trim(),
      description: taskForm.description.trim() || null,
      due_at: dueAt,
      priority: taskForm.priority,
      timezone: taskForm.timezone.trim(),
    };
    await runOnce(`todo-save-${editingTaskId ?? 'new'}`, async () => {
      const saved = editingTaskId
        ? await updateTask(controller, editingTaskId, input)
        : await createTask(controller, input);
      setTasks(current => {
        const index = current.findIndex(task => task.id === saved.id);
        if (index < 0) return [saved, ...current];
        const next = [...current];
        next[index] = saved;
        return next;
      });
      setTaskFormOpen(false);
      setNotice({ text: editingTaskId ? 'Task updated.' : 'Task created.' });
    });
  };

  const saveReminder = async () => {
    const triggerAt = buildLocalDateTime(reminderForm.date, reminderForm.time);
    if (!reminderForm.title.trim() || !triggerAt) {
      setNotice({
        text: 'Enter a title and a complete valid date/time.',
        error: true,
      });
      return;
    }
    const input = {
      title: reminderForm.title.trim(),
      body: reminderForm.body.trim() || null,
      trigger_at: triggerAt,
      timezone: reminderForm.timezone.trim(),
      recurrence_rule: PHASE7_RECURRENCE_ACCEPTED
        ? recurrenceRule(reminderForm.recurrence)
        : null,
      delivery_channel: 'push' as const,
    };
    await runOnce(`reminder-save-${editingReminderId ?? 'new'}`, async () => {
      const saved = editingReminderId
        ? await updateReminder(controller, editingReminderId, input)
        : await createReminder(controller, input);
      setReminders(current => {
        const index = current.findIndex(reminder => reminder.id === saved.id);
        if (index < 0)
          return [...current, saved].sort((a, b) =>
            a.trigger_at.localeCompare(b.trigger_at),
          );
        const next = [...current];
        next[index] = saved;
        return next;
      });
      setReminderFormOpen(false);
      setNotice({
        text: editingReminderId ? 'Reminder updated.' : 'Reminder scheduled.',
      });
    });
  };

  const resolveConfirmation = async () => {
    if (!confirmation) return;
    const current = confirmation;
    setConfirmation(null);
    if (current.kind === 'todo-complete') {
      await runOnce(`todo-complete-${current.id}`, async () => {
        const completed = await completeTask(controller, current.id);
        setTasks(items =>
          items.map(task => (task.id === completed.id ? completed : task)),
        );
        setNotice({ text: 'Task completed.' });
      });
      return;
    }
    if (current.kind === 'todo-delete') {
      await runOnce(`todo-delete-${current.id}`, async () => {
        await deleteTask(controller, current.id);
        setTasks(items => items.filter(task => task.id !== current.id));
        setNotice({ text: 'Task deleted.' });
      });
      return;
    }
    await runOnce(`reminder-delete-${current.id}`, async () => {
      await deleteReminder(controller, current.id);
      setReminders(items =>
        items.filter(reminder => reminder.id !== current.id),
      );
      setNotice({ text: 'Reminder cancelled.' });
    });
  };

  const requestPushPermission = async () => {
    if (Platform.OS !== 'android' || Number(Platform.Version) < 33) {
      setPushState('granted');
      return;
    }
    try {
      const result = await PermissionsAndroid.request(
        'android.permission.POST_NOTIFICATIONS',
      );
      setPushState(
        result === PermissionsAndroid.RESULTS.GRANTED ? 'granted' : 'denied',
      );
    } catch {
      setPushState('denied');
    }
  };

  // Agenda Grouping: Safely groups tasks by date using device-aware scheduling helpers
  const groupedTasks = useMemo(() => {
    if (taskFilter === 'completed') {
      return [{ key: 'completed', title: 'COMPLETED', dotColor: colors.success, items: visibleTasks }];
    }

    const today = dateInputForOffset(0);
    const tomorrow = dateInputForOffset(1);

    const buckets: Record<string, Task[]> = {
      today: [],
      tomorrow: [],
      upcoming: [],
      past: [],
      noDate: [],
    };

    visibleTasks.forEach(task => {
      if (!task.due_at) {
        buckets.noDate.push(task);
        return;
      }
      const parts = localInputParts(task.due_at, task.timezone);
      if (!parts.date) {
        buckets.noDate.push(task);
      } else if (parts.date === today) {
        buckets.today.push(task);
      } else if (parts.date === tomorrow) {
        buckets.tomorrow.push(task);
      } else if (parts.date > tomorrow) {
        buckets.upcoming.push(task);
      } else {
        buckets.past.push(task);
      }
    });

    const sections = [
      { key: 'today', title: 'TODAY', dotColor: colors.primary, items: buckets.today },
      { key: 'tomorrow', title: 'TOMORROW', dotColor: colors.secondary, items: buckets.tomorrow },
      { key: 'upcoming', title: 'UPCOMING', dotColor: colors.tertiary, items: buckets.upcoming },
      { key: 'past', title: 'PAST DUE', dotColor: colors.warning, items: buckets.past },
      { key: 'noDate', title: 'NO DUE DATE', dotColor: colors.disabled, items: buckets.noDate },
    ];

    return sections.filter(section => section.items.length > 0);
  }, [visibleTasks, taskFilter, colors]);

  // Agenda Grouping: Safely groups reminders by date
  const groupedReminders = useMemo(() => {
    const today = dateInputForOffset(0);
    const tomorrow = dateInputForOffset(1);

    const buckets: Record<string, Reminder[]> = {
      today: [],
      tomorrow: [],
      upcoming: [],
      past: [],
    };

    visibleReminders.forEach(reminder => {
      const parts = localInputParts(reminder.trigger_at, reminder.timezone);
      if (!parts.date) {
        buckets.upcoming.push(reminder);
      } else if (parts.date === today) {
        buckets.today.push(reminder);
      } else if (parts.date === tomorrow) {
        buckets.tomorrow.push(reminder);
      } else if (parts.date > tomorrow) {
        buckets.upcoming.push(reminder);
      } else {
        buckets.past.push(reminder);
      }
    });

    const sections = [
      { key: 'today', title: 'TODAY', dotColor: colors.primary, items: buckets.today },
      { key: 'tomorrow', title: 'TOMORROW', dotColor: colors.secondary, items: buckets.tomorrow },
      { key: 'upcoming', title: 'UPCOMING', dotColor: colors.tertiary, items: buckets.upcoming },
      { key: 'past', title: 'PREVIOUS', dotColor: colors.disabled, items: buckets.past },
    ];

    return sections.filter(section => section.items.length > 0);
  }, [visibleReminders, colors]);

  return (
    <Screen testID="tasks-screen">
      <ScrollView
        contentContainerStyle={styles.content}
        refreshControl={
          <RefreshControl
            refreshing={refreshing}
            onRefresh={() => load(true)}
          />
        }
      >
        {/* Stitch-inspired Hero Header */}
        <View style={styles.header}>
          <View style={styles.titleArea}>
            <AppText style={[styles.overline, { color: colors.primary }]}>
              AGENDA
            </AppText>
            <Heading>{strings.tasks.title}</Heading>
            <AppText style={[styles.subtitle, { color: colors.textMuted }]}>
              {page === 'tasks'
                ? `${taskCounts.upcoming} upcoming • ${taskCounts.all} total`
                : `${reminderCounts.upcoming} scheduled • ${reminderCounts.all} total`}
            </AppText>
          </View>

          {/* Floating/Primary Add Action */}
          <View style={styles.headerActionArea}>
            {page === 'tasks' ? (
              <ActionButton
                label={strings.tasks.createTask}
                onPress={openCreateTask}
                testID="todo-create"
              />
            ) : (
              <ActionButton
                label={strings.tasks.createReminder}
                onPress={openCreateReminder}
                testID="reminder-create"
              />
            )}
          </View>
        </View>

        {/* Dual-Mode Segmented Tabs & Filter Chips */}
        <TaskFilterChips
          onPageChange={setPage}
          onReminderFilterChange={setReminderFilter}
          onTaskFilterChange={setTaskFilter}
          page={page}
          reminderCounts={reminderCounts}
          reminderFilter={reminderFilter}
          taskCounts={taskCounts}
          taskFilter={taskFilter}
        />

        {notice ? (
          <StatusBanner tone={notice.error ? 'error' : 'info'}>
            {notice.text}
          </StatusBanner>
        ) : null}

        {loading ? (
          <AppText style={styles.loadingText} testID="tasks-loading">
            {strings.tasks.loading}
          </AppText>
        ) : null}

        {page === 'tasks' ? (
          <View style={styles.listContainer}>
            {groupedTasks.length > 0 ? (
              groupedTasks.map(section => (
                <View key={section.key} style={styles.section}>
                  <View style={styles.sectionHeader}>
                    <View
                      style={[
                        styles.sectionDot,
                        { backgroundColor: section.dotColor },
                      ]}
                    />
                    <AppText
                      style={[
                        styles.sectionTitle,
                        { color: colors.textMuted },
                      ]}
                    >
                      {section.title}
                    </AppText>
                    <AppText
                      style={[
                        styles.sectionCount,
                        { color: colors.textSubtle },
                      ]}
                    >
                      {section.items.length}{' '}
                      {section.items.length === 1 ? 'task' : 'tasks'}
                    </AppText>
                  </View>
                  {section.items.map(task => (
                    <TaskItemCard
                      busy={busyKey !== null}
                      key={task.id}
                      onComplete={t =>
                        setConfirmation({
                          kind: 'todo-complete',
                          id: t.id,
                          label: t.title,
                        })
                      }
                      onDelete={t =>
                        setConfirmation({
                          kind: 'todo-delete',
                          id: t.id,
                          label: t.title,
                        })
                      }
                      onEdit={openEditTask}
                      task={task}
                    />
                  ))}
                </View>
              ))
            ) : (
              <TaskEmptyState
                hint="Capture tasks naturally with voice anytime."
                testID="tasks-empty"
                text={strings.tasks.emptyTasks}
                title="All caught up!"
              />
            )}
          </View>
        ) : (
          <View style={styles.listContainer}>
            <PushPermissionCard
              onOpenSettings={() =>
                Linking.openSettings().catch(() => undefined)
              }
              onRequest={requestPushPermission}
              state={pushState}
            />

            {groupedReminders.length > 0 ? (
              groupedReminders.map(section => (
                <View key={section.key} style={styles.section}>
                  <View style={styles.sectionHeader}>
                    <View
                      style={[
                        styles.sectionDot,
                        { backgroundColor: section.dotColor },
                      ]}
                    />
                    <AppText
                      style={[
                        styles.sectionTitle,
                        { color: colors.textMuted },
                      ]}
                    >
                      {section.title}
                    </AppText>
                    <AppText
                      style={[
                        styles.sectionCount,
                        { color: colors.textSubtle },
                      ]}
                    >
                      {section.items.length}{' '}
                      {section.items.length === 1 ? 'reminder' : 'reminders'}
                    </AppText>
                  </View>
                  {section.items.map(reminder => (
                    <ReminderItemCard
                      busy={busyKey !== null}
                      key={reminder.id}
                      onDelete={r =>
                        setConfirmation({
                          kind: 'reminder-delete',
                          id: r.id,
                          label: r.title,
                        })
                      }
                      onEdit={openEditReminder}
                      reminder={reminder}
                    />
                  ))}
                </View>
              ))
            ) : (
              <TaskEmptyState
                hint="Say 'Remind me tomorrow at 9 AM' to create a reminder."
                testID="reminders-empty"
                text={strings.tasks.emptyReminders}
                title="No reminders scheduled"
              />
            )}
          </View>
        )}
      </ScrollView>

      {/* Create / Edit Modals */}
      <TaskEditorModal
        draft={taskForm}
        mode={taskFormMode}
        onChange={setTaskForm}
        onClose={() => setTaskFormOpen(false)}
        onSave={saveTask}
        saving={busyKey?.startsWith('todo-save-') ?? false}
        visible={taskFormOpen}
      />

      <ReminderEditorModal
        draft={reminderForm}
        mode={reminderFormMode}
        onChange={setReminderForm}
        onClose={() => setReminderFormOpen(false)}
        onSave={saveReminder}
        saving={busyKey?.startsWith('reminder-save-') ?? false}
        visible={reminderFormOpen}
      />

      {/* Confirmation Modal */}
      <ConfirmModal
        busy={busyKey !== null}
        confirmation={confirmation}
        onCancel={() => setConfirmation(null)}
        onConfirm={resolveConfirmation}
      />
    </Screen>
  );
}

const styles = StyleSheet.create({
  content: {
    gap: spacing.md,
    paddingBottom: spacing.xxl,
  },
  header: {
    alignItems: 'flex-start',
    flexDirection: 'row',
    justifyContent: 'space-between',
    gap: spacing.sm,
  },
  titleArea: {
    flex: 1,
    gap: 2,
  },
  overline: {
    fontSize: typography.caption,
    fontWeight: '700',
    letterSpacing: 1,
  },
  subtitle: {
    fontSize: typography.caption,
  },
  headerActionArea: {
    alignItems: 'flex-end',
  },
  loadingText: {
    fontSize: typography.body,
    paddingVertical: spacing.sm,
  },
  listContainer: {
    gap: spacing.md,
  },
  section: {
    gap: spacing.xs,
    marginTop: spacing.xs,
  },
  sectionHeader: {
    alignItems: 'center',
    flexDirection: 'row',
    gap: spacing.xs,
    paddingHorizontal: 2,
  },
  sectionDot: {
    borderRadius: radii.full,
    height: 8,
    width: 8,
  },
  sectionTitle: {
    fontSize: typography.caption,
    fontWeight: '700',
    letterSpacing: 0.8,
  },
  sectionCount: {
    fontSize: typography.caption,
    marginLeft: 'auto',
  },
});
