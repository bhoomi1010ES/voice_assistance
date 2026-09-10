import React, {
  useCallback,
  useEffect,
  useMemo,
  useRef,
  useState,
} from 'react';
import {
  Linking,
  Modal,
  PermissionsAndroid,
  Platform,
  Pressable,
  RefreshControl,
  ScrollView,
  StyleSheet,
  TextInput,
  View,
} from 'react-native';
import { useAuth } from '../auth/AuthProvider';
import { safeUserMessage, toClientError } from '../api/errors';
import {
  ActionButton,
  AppText,
  Card,
  Heading,
  Screen,
  StatusBanner,
} from '../components/ui/Primitives';
import { useAppTheme } from '../design/ThemeProvider';
import { spacing, typography } from '../design/tokens';
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
  dateInputForOffset,
  buildLocalDateTime,
  deviceTimezone,
  formatScheduledTime,
  localInputParts,
  recurrenceChoice,
  recurrenceRule,
  schedulePreview,
} from '../tasks/scheduling';
import { Reminder, Task, TaskPriority } from '../tasks/types';

type Page = 'tasks' | 'reminders';
type TaskFilter = 'upcoming' | 'all' | 'completed';
type ReminderFilter = 'upcoming' | 'all' | 'sent' | 'failed';
type FormMode = 'create' | 'edit';
type Confirmation =
  | { kind: 'todo-delete'; id: string; label: string }
  | { kind: 'todo-complete'; id: string; label: string }
  | { kind: 'reminder-delete'; id: string; label: string };

type TaskDraft = {
  title: string;
  description: string;
  date: string;
  time: string;
  timezone: string;
  priority: TaskPriority;
};

type ReminderDraft = {
  title: string;
  body: string;
  date: string;
  time: string;
  timezone: string;
  recurrence: 'none' | 'daily' | 'weekly';
};

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
  const [taskFormMode, setTaskFormMode] = useState<FormMode>('create');
  const [reminderFormMode, setReminderFormMode] = useState<FormMode>('create');
  const [editingTaskId, setEditingTaskId] = useState<string | null>(null);
  const [editingReminderId, setEditingReminderId] = useState<string | null>(
    null,
  );
  const [taskFormOpen, setTaskFormOpen] = useState(false);
  const [reminderFormOpen, setReminderFormOpen] = useState(false);
  const [confirmation, setConfirmation] = useState<Confirmation | null>(null);
  const [pushState, setPushState] = useState<'unknown' | 'granted' | 'denied'>(
    'unknown',
  );
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
        <Heading>{strings.tasks.title}</Heading>
        <AppText style={styles.subtitle}>{strings.tasks.body}</AppText>

        <View style={styles.pageTabs} accessibilityRole="tablist">
          <TabButton
            label={strings.tasks.tasksTab}
            selected={page === 'tasks'}
            onPress={() => setPage('tasks')}
            testID="tasks-tab"
          />
          <TabButton
            label={strings.tasks.remindersTab}
            selected={page === 'reminders'}
            onPress={() => setPage('reminders')}
            testID="reminders-tab"
          />
        </View>

        {notice ? (
          <StatusBanner tone={notice.error ? 'error' : 'info'}>
            {notice.text}
          </StatusBanner>
        ) : null}
        {loading ? (
          <AppText testID="tasks-loading">{strings.tasks.loading}</AppText>
        ) : null}

        {page === 'tasks' ? (
          <TaskPage
            filter={taskFilter}
            tasks={visibleTasks}
            busyKey={busyKey}
            onFilter={setTaskFilter}
            onCreate={openCreateTask}
            onEdit={openEditTask}
            onComplete={task =>
              setConfirmation({
                kind: 'todo-complete',
                id: task.id,
                label: task.title,
              })
            }
            onDelete={task =>
              setConfirmation({
                kind: 'todo-delete',
                id: task.id,
                label: task.title,
              })
            }
          />
        ) : (
          <ReminderPage
            filter={reminderFilter}
            reminders={visibleReminders}
            busyKey={busyKey}
            onFilter={setReminderFilter}
            onCreate={openCreateReminder}
            onEdit={openEditReminder}
            onDelete={reminder =>
              setConfirmation({
                kind: 'reminder-delete',
                id: reminder.id,
                label: reminder.title,
              })
            }
            pushState={pushState}
            onRequestPush={requestPushPermission}
            onOpenSettings={() => Linking.openSettings().catch(() => undefined)}
          />
        )}
      </ScrollView>

      <TaskFormModal
        visible={taskFormOpen}
        mode={taskFormMode}
        draft={taskForm}
        saving={busyKey?.startsWith('todo-save-') ?? false}
        onChange={setTaskForm}
        onClose={() => setTaskFormOpen(false)}
        onSave={saveTask}
      />
      <ReminderFormModal
        visible={reminderFormOpen}
        mode={reminderFormMode}
        draft={reminderForm}
        saving={busyKey?.startsWith('reminder-save-') ?? false}
        onChange={setReminderForm}
        onClose={() => setReminderFormOpen(false)}
        onSave={saveReminder}
      />
      <ConfirmModal
        confirmation={confirmation}
        busy={busyKey !== null}
        onCancel={() => setConfirmation(null)}
        onConfirm={resolveConfirmation}
      />
    </Screen>
  );
}

function TaskPage({
  filter,
  tasks,
  busyKey,
  onFilter,
  onCreate,
  onEdit,
  onComplete,
  onDelete,
}: {
  filter: TaskFilter;
  tasks: Task[];
  busyKey: string | null;
  onFilter: (value: TaskFilter) => void;
  onCreate: () => void;
  onEdit: (task: Task) => void;
  onComplete: (task: Task) => void;
  onDelete: (task: Task) => void;
}) {
  return (
    <View>
      <View style={styles.filterRow} accessibilityRole="tablist">
        <TabButton
          label={strings.tasks.upcoming}
          selected={filter === 'upcoming'}
          onPress={() => onFilter('upcoming')}
          testID="tasks-filter-upcoming"
        />
        <TabButton
          label={strings.tasks.all}
          selected={filter === 'all'}
          onPress={() => onFilter('all')}
          testID="tasks-filter-all"
        />
        <TabButton
          label={strings.tasks.completed}
          selected={filter === 'completed'}
          onPress={() => onFilter('completed')}
          testID="tasks-filter-completed"
        />
      </View>
      <ActionButton
        label={strings.tasks.createTask}
        onPress={onCreate}
        testID="todo-create"
      />
      {tasks.length ? (
        tasks.map(task => (
          <TaskCard
            key={task.id}
            task={task}
            busy={busyKey !== null}
            onEdit={onEdit}
            onComplete={onComplete}
            onDelete={onDelete}
          />
        ))
      ) : (
        <EmptyState text={strings.tasks.emptyTasks} testID="tasks-empty" />
      )}
    </View>
  );
}

function ReminderPage({
  filter,
  reminders,
  busyKey,
  onFilter,
  onCreate,
  onEdit,
  onDelete,
  pushState,
  onRequestPush,
  onOpenSettings,
}: {
  filter: ReminderFilter;
  reminders: Reminder[];
  busyKey: string | null;
  onFilter: (value: ReminderFilter) => void;
  onCreate: () => void;
  onEdit: (reminder: Reminder) => void;
  onDelete: (reminder: Reminder) => void;
  pushState: 'unknown' | 'granted' | 'denied';
  onRequestPush: () => void;
  onOpenSettings: () => void;
}) {
  return (
    <View>
      <PushPermissionCard
        state={pushState}
        onRequest={onRequestPush}
        onOpenSettings={onOpenSettings}
      />
      <View style={styles.filterRow} accessibilityRole="tablist">
        <TabButton
          label={strings.tasks.upcoming}
          selected={filter === 'upcoming'}
          onPress={() => onFilter('upcoming')}
          testID="reminders-filter-upcoming"
        />
        <TabButton
          label={strings.tasks.all}
          selected={filter === 'all'}
          onPress={() => onFilter('all')}
          testID="reminders-filter-all"
        />
        <TabButton
          label={strings.tasks.sent}
          selected={filter === 'sent'}
          onPress={() => onFilter('sent')}
          testID="reminders-filter-sent"
        />
        <TabButton
          label={strings.tasks.failed}
          selected={filter === 'failed'}
          onPress={() => onFilter('failed')}
          testID="reminders-filter-failed"
        />
      </View>
      <ActionButton
        label={strings.tasks.createReminder}
        onPress={onCreate}
        testID="reminder-create"
      />
      {reminders.length ? (
        reminders.map(reminder => (
          <ReminderCard
            key={reminder.id}
            reminder={reminder}
            busy={busyKey !== null}
            onEdit={onEdit}
            onDelete={onDelete}
          />
        ))
      ) : (
        <EmptyState
          text={strings.tasks.emptyReminders}
          testID="reminders-empty"
        />
      )}
    </View>
  );
}

function TaskCard({
  task,
  busy,
  onEdit,
  onComplete,
  onDelete,
}: {
  task: Task;
  busy: boolean;
  onEdit: (task: Task) => void;
  onComplete: (task: Task) => void;
  onDelete: (task: Task) => void;
}) {
  return (
    <Card style={styles.itemCard} testID={`todo-${task.id}`}>
      <AppText style={styles.itemTitle}>{task.title}</AppText>
      {task.description ? <AppText>{task.description}</AppText> : null}
      <AppText style={styles.meta}>
        {task.status} · {task.priority}
      </AppText>
      <AppText style={styles.schedule}>
        {formatScheduledTime(task.due_at, task.timezone)} · {task.timezone}
      </AppText>
      <View style={styles.itemActions}>
        {task.status !== 'completed' ? (
          <ActionButton
            label={strings.tasks.complete}
            onPress={() => onComplete(task)}
            disabled={busy}
            variant="secondary"
            testID={`todo-complete-${task.id}`}
          />
        ) : null}
        <ActionButton
          label={strings.tasks.edit}
          onPress={() => onEdit(task)}
          disabled={busy}
          variant="quiet"
          testID={`todo-edit-${task.id}`}
        />
        <ActionButton
          label={strings.tasks.delete}
          onPress={() => onDelete(task)}
          disabled={busy}
          variant="quiet"
          testID={`todo-delete-${task.id}`}
        />
      </View>
    </Card>
  );
}

function ReminderCard({
  reminder,
  busy,
  onEdit,
  onDelete,
}: {
  reminder: Reminder;
  busy: boolean;
  onEdit: (reminder: Reminder) => void;
  onDelete: (reminder: Reminder) => void;
}) {
  return (
    <Card style={styles.itemCard} testID={`reminder-${reminder.id}`}>
      <AppText style={styles.itemTitle}>{reminder.title}</AppText>
      {reminder.body ? <AppText>{reminder.body}</AppText> : null}
      <AppText style={styles.meta}>
        {reminder.status}
        {reminder.recurrence_rule ? ` · ${reminder.recurrence_rule}` : ''}
      </AppText>
      <AppText style={styles.schedule}>
        {formatScheduledTime(reminder.trigger_at, reminder.timezone)} ·{' '}
        {reminder.timezone}
      </AppText>
      {reminder.status === 'failed' ? (
        <StatusBanner tone="error">{strings.tasks.deliveryFailed}</StatusBanner>
      ) : null}
      <View style={styles.itemActions}>
        {reminder.status === 'scheduled' ? (
          <ActionButton
            label={strings.tasks.edit}
            onPress={() => onEdit(reminder)}
            disabled={busy}
            variant="secondary"
            testID={`reminder-edit-${reminder.id}`}
          />
        ) : null}
        {reminder.status === 'scheduled' ? (
          <ActionButton
            label={strings.tasks.cancelReminder}
            onPress={() => onDelete(reminder)}
            disabled={busy}
            variant="quiet"
            testID={`reminder-delete-${reminder.id}`}
          />
        ) : null}
      </View>
    </Card>
  );
}

function PushPermissionCard({
  state,
  onRequest,
  onOpenSettings,
}: {
  state: 'unknown' | 'granted' | 'denied';
  onRequest: () => void;
  onOpenSettings: () => void;
}) {
  return (
    <Card style={styles.pushCard} testID="push-permission-card">
      <AppText style={styles.itemTitle}>{strings.tasks.pushTitle}</AppText>
      <AppText>
        {state === 'granted'
          ? strings.tasks.pushEnabled
          : state === 'denied'
          ? strings.tasks.pushDenied
          : strings.tasks.pushDescription}
      </AppText>
      {state === 'denied' ? (
        <ActionButton
          label={strings.tasks.openSettings}
          onPress={onOpenSettings}
          variant="secondary"
          testID="push-open-settings"
        />
      ) : state !== 'granted' ? (
        <ActionButton
          label={strings.tasks.enablePush}
          onPress={onRequest}
          variant="secondary"
          testID="push-enable"
        />
      ) : null}
    </Card>
  );
}

function TaskFormModal({
  visible,
  mode,
  draft,
  saving,
  onChange,
  onClose,
  onSave,
}: {
  visible: boolean;
  mode: FormMode;
  draft: TaskDraft;
  saving: boolean;
  onChange: (value: TaskDraft) => void;
  onClose: () => void;
  onSave: () => void;
}) {
  return (
    <Modal animationType="slide" onRequestClose={onClose} visible={visible}>
      <Screen>
        <ScrollView contentContainerStyle={styles.modalContent}>
          <Heading>
            {mode === 'create'
              ? strings.tasks.createTask
              : strings.tasks.editTask}
          </Heading>
          <Field
            label={strings.tasks.titleLabel}
            value={draft.title}
            onChangeText={title => onChange({ ...draft, title })}
            testID="todo-title-input"
          />
          <Field
            label={strings.tasks.descriptionLabel}
            value={draft.description}
            onChangeText={description => onChange({ ...draft, description })}
            multiline
            testID="todo-description-input"
          />
          <ScheduleFields draft={draft} onChange={onChange} prefix="task" />
          <AppText style={styles.previewLabel}>{strings.tasks.preview}</AppText>
          <AppText testID="todo-schedule-preview">
            {schedulePreview(draft.date, draft.time, draft.timezone)}
          </AppText>
          <View style={styles.modalActions}>
            <ActionButton
              label={strings.tasks.cancel}
              onPress={onClose}
              variant="quiet"
            />
            <ActionButton
              label={saving ? strings.tasks.saving : strings.tasks.save}
              onPress={onSave}
              disabled={saving}
              testID="todo-save"
            />
          </View>
        </ScrollView>
      </Screen>
    </Modal>
  );
}

function ReminderFormModal({
  visible,
  mode,
  draft,
  saving,
  onChange,
  onClose,
  onSave,
}: {
  visible: boolean;
  mode: FormMode;
  draft: ReminderDraft;
  saving: boolean;
  onChange: (value: ReminderDraft) => void;
  onClose: () => void;
  onSave: () => void;
}) {
  return (
    <Modal animationType="slide" onRequestClose={onClose} visible={visible}>
      <Screen>
        <ScrollView contentContainerStyle={styles.modalContent}>
          <Heading>
            {mode === 'create'
              ? strings.tasks.createReminder
              : strings.tasks.editReminder}
          </Heading>
          <Field
            label={strings.tasks.titleLabel}
            value={draft.title}
            onChangeText={title => onChange({ ...draft, title })}
            testID="reminder-title-input"
          />
          <Field
            label={strings.tasks.bodyLabel}
            value={draft.body}
            onChangeText={body => onChange({ ...draft, body })}
            multiline
            testID="reminder-body-input"
          />
          <ScheduleFields draft={draft} onChange={onChange} prefix="reminder" />
          <AppText style={styles.previewLabel}>{strings.tasks.preview}</AppText>
          <AppText testID="reminder-schedule-preview">
            {schedulePreview(draft.date, draft.time, draft.timezone)}
          </AppText>
          <AppText style={styles.previewHelp}>
            {strings.tasks.serverValidation}
          </AppText>
          {PHASE7_RECURRENCE_ACCEPTED ? (
            <View style={styles.recurrence}>
              <AppText style={styles.fieldLabel}>
                {strings.tasks.recurrence}
              </AppText>
              {(['none', 'daily', 'weekly'] as const).map(choice => (
                <ActionButton
                  key={choice}
                  label={choice}
                  onPress={() => onChange({ ...draft, recurrence: choice })}
                  variant={
                    draft.recurrence === choice ? 'primary' : 'secondary'
                  }
                  testID={`recurrence-${choice}`}
                />
              ))}
            </View>
          ) : null}
          <View style={styles.modalActions}>
            <ActionButton
              label={strings.tasks.cancel}
              onPress={onClose}
              variant="quiet"
            />
            <ActionButton
              label={saving ? strings.tasks.saving : strings.tasks.save}
              onPress={onSave}
              disabled={saving}
              testID="reminder-save"
            />
          </View>
        </ScrollView>
      </Screen>
    </Modal>
  );
}

function ScheduleFields<
  T extends { date: string; time: string; timezone: string },
>({
  draft,
  onChange,
  prefix,
}: {
  draft: T;
  onChange: (value: T) => void;
  prefix: string;
}) {
  return (
    <>
      <Field
        label={strings.tasks.dateLabel}
        value={draft.date}
        placeholder="YYYY-MM-DD"
        onChangeText={date => onChange({ ...draft, date })}
        testID={`${prefix}-date-input`}
      />
      <View style={styles.quickDateRow}>
        <ActionButton
          label={strings.tasks.tomorrow}
          onPress={() => onChange({ ...draft, date: dateInputForOffset(1) })}
          variant="secondary"
          testID={`${prefix}-tomorrow`}
        />
      </View>
      <Field
        label={strings.tasks.timeLabel}
        value={draft.time}
        placeholder="HH:mm"
        onChangeText={time => onChange({ ...draft, time })}
        testID={`${prefix}-time-input`}
      />
      <Field
        label={strings.tasks.timezoneLabel}
        value={draft.timezone}
        onChangeText={timezone => onChange({ ...draft, timezone })}
        testID={`${prefix}-timezone-input`}
      />
    </>
  );
}

function Field({
  label,
  value,
  onChangeText,
  testID,
  placeholder,
  multiline = false,
}: {
  label: string;
  value: string;
  onChangeText: (value: string) => void;
  testID: string;
  placeholder?: string;
  multiline?: boolean;
}) {
  const { colors } = useAppTheme();
  return (
    <View style={styles.field}>
      <AppText style={styles.fieldLabel}>{label}</AppText>
      <TextInput
        accessibilityLabel={label}
        multiline={multiline}
        onChangeText={onChangeText}
        placeholder={placeholder}
        placeholderTextColor={colors.textMuted}
        style={[
          styles.input,
          { color: colors.text, borderColor: colors.border },
        ]}
        testID={testID}
        value={value}
      />
    </View>
  );
}

function ConfirmModal({
  confirmation,
  busy,
  onCancel,
  onConfirm,
}: {
  confirmation: Confirmation | null;
  busy: boolean;
  onCancel: () => void;
  onConfirm: () => void;
}) {
  return (
    <Modal
      animationType="fade"
      onRequestClose={onCancel}
      transparent
      visible={Boolean(confirmation)}
    >
      <View style={styles.confirmOverlay}>
        <Card style={styles.confirmCard}>
          <Heading>{strings.tasks.confirmTitle}</Heading>
          <AppText>
            {confirmation
              ? `${strings.tasks.confirmBody} “${confirmation.label}”`
              : ''}
          </AppText>
          <View style={styles.modalActions}>
            <ActionButton
              label={strings.tasks.cancel}
              onPress={onCancel}
              variant="quiet"
              testID="action-cancel"
            />
            <ActionButton
              label={strings.tasks.confirm}
              onPress={onConfirm}
              disabled={busy}
              testID="action-confirm"
            />
          </View>
        </Card>
      </View>
    </Modal>
  );
}

function TabButton({
  label,
  selected,
  onPress,
  testID,
}: {
  label: string;
  selected: boolean;
  onPress: () => void;
  testID: string;
}) {
  const { colors } = useAppTheme();
  return (
    <Pressable
      accessibilityRole="tab"
      accessibilityState={{ selected }}
      onPress={onPress}
      style={[
        styles.tab,
        {
          borderColor: colors.border,
          backgroundColor: selected ? colors.accent : colors.surfaceMuted,
        },
      ]}
      testID={testID}
    >
      <AppText style={{ color: selected ? colors.accentText : colors.text }}>
        {label}
      </AppText>
    </Pressable>
  );
}

function EmptyState({ text, testID }: { text: string; testID: string }) {
  return (
    <Card style={styles.empty} testID={testID}>
      <AppText>{text}</AppText>
    </Card>
  );
}

const styles = StyleSheet.create({
  content: { gap: spacing.md, paddingBottom: spacing.xxl },
  subtitle: { color: '#5D6875' },
  pageTabs: { flexDirection: 'row', gap: spacing.sm },
  filterRow: { flexDirection: 'row', flexWrap: 'wrap', gap: spacing.sm },
  tab: {
    borderRadius: 999,
    borderWidth: 1,
    minHeight: 44,
    justifyContent: 'center',
    paddingHorizontal: spacing.md,
  },
  itemCard: { gap: spacing.sm, marginTop: spacing.md },
  pushCard: { gap: spacing.sm, marginTop: spacing.md },
  itemTitle: { fontSize: typography.heading, fontWeight: '700' },
  meta: {
    color: '#5D6875',
    fontSize: typography.caption,
    textTransform: 'capitalize',
  },
  schedule: { fontWeight: '600' },
  itemActions: {
    flexDirection: 'row',
    flexWrap: 'wrap',
    gap: spacing.sm,
    marginTop: spacing.sm,
  },
  empty: { marginTop: spacing.md },
  field: { gap: spacing.xs, marginTop: spacing.md },
  fieldLabel: { fontWeight: '700' },
  input: {
    borderRadius: 10,
    borderWidth: 1,
    fontSize: typography.body,
    minHeight: 48,
    paddingHorizontal: spacing.md,
    paddingVertical: spacing.sm,
  },
  quickDateRow: { alignItems: 'flex-start', marginTop: spacing.sm },
  previewLabel: { fontWeight: '700', marginTop: spacing.lg },
  previewHelp: {
    color: '#5D6875',
    fontSize: typography.caption,
    marginTop: spacing.xs,
  },
  recurrence: { gap: spacing.sm, marginTop: spacing.lg },
  modalContent: { paddingBottom: spacing.xxl },
  modalActions: {
    flexDirection: 'row',
    flexWrap: 'wrap',
    gap: spacing.sm,
    justifyContent: 'flex-end',
    marginTop: spacing.lg,
  },
  confirmOverlay: {
    alignItems: 'center',
    backgroundColor: '#00000066',
    flex: 1,
    justifyContent: 'center',
    padding: spacing.lg,
  },
  confirmCard: { gap: spacing.md, width: '100%' },
});
