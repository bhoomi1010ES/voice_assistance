import { AuthController } from '../auth/AuthController';
import {
  CreateReminderInput,
  CreateTaskInput,
  Reminder,
  ReminderStatus,
  Task,
  TaskStatus,
  UpdateReminderInput,
  UpdateTaskInput,
} from './types';

function queryString(
  values: Record<string, string | number | boolean | undefined>,
) {
  const parts = Object.entries(values)
    .filter(([, value]) => value !== undefined)
    .map(
      ([key, value]) =>
        `${encodeURIComponent(key)}=${encodeURIComponent(String(value))}`,
    );
  return parts.length ? `?${parts.join('&')}` : '';
}

export function listTasks(
  controller: AuthController,
  options: { status?: TaskStatus; priority?: string; limit?: number } = {},
): Promise<Task[]> {
  return controller.request<Task[]>(
    `/tasks${queryString({
      status: options.status,
      priority: options.priority,
      limit: options.limit ?? 100,
    })}`,
  );
}

export function createTask(
  controller: AuthController,
  input: CreateTaskInput,
): Promise<Task> {
  return controller.request<Task>('/tasks', {
    method: 'POST',
    body: JSON.stringify(input),
  });
}

export function updateTask(
  controller: AuthController,
  taskId: string,
  input: UpdateTaskInput,
): Promise<Task> {
  return controller.request<Task>(`/tasks/${encodeURIComponent(taskId)}`, {
    method: 'PATCH',
    body: JSON.stringify(input),
  });
}

export function completeTask(
  controller: AuthController,
  taskId: string,
): Promise<Task> {
  return controller.request<Task>(
    `/tasks/${encodeURIComponent(taskId)}/complete`,
    { method: 'POST' },
  );
}

export async function deleteTask(
  controller: AuthController,
  taskId: string,
): Promise<void> {
  await controller.request<null>(`/tasks/${encodeURIComponent(taskId)}`, {
    method: 'DELETE',
  });
}

export function listReminders(
  controller: AuthController,
  options: {
    status?: ReminderStatus;
    upcoming?: boolean;
    limit?: number;
  } = {},
): Promise<Reminder[]> {
  return controller.request<Reminder[]>(
    `/reminders${queryString({
      status: options.status,
      upcoming: options.upcoming,
      limit: options.limit ?? 100,
    })}`,
  );
}

export function createReminder(
  controller: AuthController,
  input: CreateReminderInput,
): Promise<Reminder> {
  return controller.request<Reminder>('/reminders', {
    method: 'POST',
    body: JSON.stringify(input),
  });
}

export function updateReminder(
  controller: AuthController,
  reminderId: string,
  input: UpdateReminderInput,
): Promise<Reminder> {
  return controller.request<Reminder>(
    `/reminders/${encodeURIComponent(reminderId)}`,
    {
      method: 'PATCH',
      body: JSON.stringify(input),
    },
  );
}

export async function deleteReminder(
  controller: AuthController,
  reminderId: string,
): Promise<void> {
  await controller.request<null>(
    `/reminders/${encodeURIComponent(reminderId)}`,
    { method: 'DELETE' },
  );
}
