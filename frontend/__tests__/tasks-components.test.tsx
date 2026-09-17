import React from 'react';
import ReactTestRenderer, { act } from 'react-test-renderer';
import { TaskItemCard } from '../src/components/tasks/TaskItemCard';
import { ReminderItemCard } from '../src/components/tasks/ReminderItemCard';
import { TaskFilterChips } from '../src/components/tasks/TaskFilterChips';
import { TaskEmptyState } from '../src/components/tasks/TaskEmptyState';
import { ConfirmModal } from '../src/components/tasks/ConfirmModal';
import { PushPermissionCard } from '../src/components/tasks/PushPermissionCard';
import { TestProviders } from '../src/testing/TestProviders';
import { Task, Reminder } from '../src/tasks/types';

describe('Tasks Presentational Components', () => {
  const sampleTask: Task = {
    id: 'task-100',
    title: 'Review Q4 guidelines',
    description: 'Check typography and color contrast',
    status: 'pending',
    priority: 'high',
    due_at: '2026-10-23T14:30:00Z',
    local_due_at: null,
    timezone: 'Asia/Kolkata',
    timezone_source: 'user',
    source_turn_id: 'turn-voice-123',
    created_at: '2026-10-20T00:00:00Z',
    updated_at: '2026-10-20T00:00:00Z',
    completed_at: null,
  };

  const sampleReminder: Reminder = {
    id: 'reminder-200',
    task_id: null,
    title: 'Standup call',
    body: 'Join via meeting link',
    trigger_at: '2026-10-24T04:30:00Z',
    local_trigger_at: null,
    timezone: 'Asia/Kolkata',
    timezone_source: 'user',
    recurrence_rule: 'FREQ=DAILY',
    status: 'scheduled',
    delivery_channel: 'push',
    created_at: '2026-10-20T00:00:00Z',
    updated_at: '2026-10-20T00:00:00Z',
    sent_at: null,
    failure_code: null,
  };

  test('TaskItemCard renders title, description, priority badge, and voice badge', () => {
    const onEdit = jest.fn();
    const onComplete = jest.fn();
    const onDelete = jest.fn();

    let renderer: ReactTestRenderer.ReactTestRenderer;
    act(() => {
      renderer = ReactTestRenderer.create(
        <TestProviders>
          <TaskItemCard
            busy={false}
            onComplete={onComplete}
            onDelete={onDelete}
            onEdit={onEdit}
            task={sampleTask}
          />
        </TestProviders>,
      );
    });

    const root = renderer!.root;
    expect(root.findByProps({ testID: 'todo-task-100' })).toBeTruthy();
    expect(root.findByProps({ testID: 'todo-complete-task-100' })).toBeTruthy();
    expect(root.findByProps({ testID: 'todo-edit-task-100' })).toBeTruthy();
    expect(root.findByProps({ testID: 'todo-delete-task-100' })).toBeTruthy();

    // Verify checkbox triggers onComplete
    act(() => {
      root.findByProps({ accessibilityRole: 'checkbox' }).props.onPress();
    });
    expect(onComplete).toHaveBeenCalledWith(sampleTask);

    // Verify complete action button triggers onComplete
    act(() => {
      root.findByProps({ testID: 'todo-complete-task-100' }).props.onPress();
    });
    expect(onComplete).toHaveBeenCalledTimes(2);

    // Verify voice badge rendered because source_turn_id is present
    expect(root.findByProps({ testID: 'todo-voice-badge-task-100' })).toBeTruthy();
  });

  test('TaskItemCard hides voice badge when source_turn_id is null', () => {
    const noVoiceTask: Task = { ...sampleTask, source_turn_id: null };
    let renderer: ReactTestRenderer.ReactTestRenderer;
    act(() => {
      renderer = ReactTestRenderer.create(
        <TestProviders>
          <TaskItemCard
            busy={false}
            onComplete={jest.fn()}
            onDelete={jest.fn()}
            onEdit={jest.fn()}
            task={noVoiceTask}
          />
        </TestProviders>,
      );
    });

    const root = renderer!.root;
    expect(root.findAllByProps({ testID: 'todo-voice-badge-task-100' })).toHaveLength(0);
  });

  test('ReminderItemCard renders title, recurrence, schedule, and actions', () => {
    const onEdit = jest.fn();
    const onDelete = jest.fn();

    let renderer: ReactTestRenderer.ReactTestRenderer;
    act(() => {
      renderer = ReactTestRenderer.create(
        <TestProviders>
          <ReminderItemCard
            busy={false}
            onDelete={onDelete}
            onEdit={onEdit}
            reminder={sampleReminder}
          />
        </TestProviders>,
      );
    });

    const root = renderer!.root;
    expect(root.findByProps({ testID: 'reminder-reminder-200' })).toBeTruthy();
    expect(root.findByProps({ testID: 'reminder-edit-reminder-200' })).toBeTruthy();
    expect(root.findByProps({ testID: 'reminder-delete-reminder-200' })).toBeTruthy();

    act(() => {
      root.findByProps({ testID: 'reminder-edit-reminder-200' }).props.onPress();
    });
    expect(onEdit).toHaveBeenCalledWith(sampleReminder);

    act(() => {
      root.findByProps({ testID: 'reminder-delete-reminder-200' }).props.onPress();
    });
    expect(onDelete).toHaveBeenCalledWith(sampleReminder);
  });

  test('TaskFilterChips handles mode and filter transitions', () => {
    const onPageChange = jest.fn();
    const onTaskFilterChange = jest.fn();
    const onReminderFilterChange = jest.fn();

    let renderer: ReactTestRenderer.ReactTestRenderer;
    act(() => {
      renderer = ReactTestRenderer.create(
        <TestProviders>
          <TaskFilterChips
            onPageChange={onPageChange}
            onReminderFilterChange={onReminderFilterChange}
            onTaskFilterChange={onTaskFilterChange}
            page="tasks"
            reminderFilter="upcoming"
            taskFilter="upcoming"
          />
        </TestProviders>,
      );
    });

    const root = renderer!.root;
    act(() => {
      root.findByProps({ testID: 'reminders-tab' }).props.onPress();
    });
    expect(onPageChange).toHaveBeenCalledWith('reminders');

    act(() => {
      root.findByProps({ testID: 'tasks-filter-completed' }).props.onPress();
    });
    expect(onTaskFilterChange).toHaveBeenCalledWith('completed');
  });

  test('ConfirmModal executes confirm and cancel callbacks', () => {
    const onConfirm = jest.fn();
    const onCancel = jest.fn();

    let renderer: ReactTestRenderer.ReactTestRenderer;
    act(() => {
      renderer = ReactTestRenderer.create(
        <TestProviders>
          <ConfirmModal
            busy={false}
            confirmation={{ kind: 'todo-delete', id: 'task-1', label: 'Draft' }}
            onCancel={onCancel}
            onConfirm={onConfirm}
          />
        </TestProviders>,
      );
    });

    const root = renderer!.root;
    act(() => {
      root.findByProps({ testID: 'action-confirm' }).props.onPress();
    });
    expect(onConfirm).toHaveBeenCalled();

    act(() => {
      root.findByProps({ testID: 'action-cancel' }).props.onPress();
    });
    expect(onCancel).toHaveBeenCalled();
  });

  test('PushPermissionCard renders settings button when denied', () => {
    const onOpenSettings = jest.fn();
    const onRequest = jest.fn();

    let renderer: ReactTestRenderer.ReactTestRenderer;
    act(() => {
      renderer = ReactTestRenderer.create(
        <TestProviders>
          <PushPermissionCard
            onOpenSettings={onOpenSettings}
            onRequest={onRequest}
            state="denied"
          />
        </TestProviders>,
      );
    });

    const root = renderer!.root;
    expect(root.findByProps({ testID: 'push-open-settings' })).toBeTruthy();
    act(() => {
      root.findByProps({ testID: 'push-open-settings' }).props.onPress();
    });
    expect(onOpenSettings).toHaveBeenCalled();
  });

  test('TaskEmptyState renders with expected testIDs', () => {
    let renderer: ReactTestRenderer.ReactTestRenderer;
    act(() => {
      renderer = ReactTestRenderer.create(
        <TestProviders>
          <TaskEmptyState testID="tasks-empty" text="No tasks found" />
        </TestProviders>,
      );
    });

    expect(renderer!.root.findByProps({ testID: 'tasks-empty' })).toBeTruthy();
  });
});
