export type TaskStatus = 'pending' | 'in_progress' | 'completed' | 'cancelled';
export type TaskPriority = 'low' | 'normal' | 'high' | 'urgent';

export type Task = {
  plan_id?: string | null;
  planning_action_id?: string | null;
  revision?: number;
  id: string;
  title: string;
  description: string | null;
  status: TaskStatus;
  priority: TaskPriority;
  due_at: string | null;
  local_due_at: string | null;
  timezone: string;
  timezone_source: string;
  source_turn_id: string | null;
  created_at: string;
  updated_at: string;
  completed_at: string | null;
};

export type ReminderStatus = 'scheduled' | 'sent' | 'failed' | 'cancelled';

export type Reminder = {
  plan_id?: string | null;
  planning_action_id?: string | null;
  revision?: number;
  id: string;
  task_id: string | null;
  title: string;
  body: string | null;
  trigger_at: string;
  local_trigger_at: string | null;
  timezone: string;
  timezone_source: string;
  recurrence_rule: string | null;
  status: ReminderStatus;
  delivery_channel: 'push' | string;
  created_at: string;
  updated_at: string;
  sent_at: string | null;
  failure_code: string | null;
};

export type CreateTaskInput = {
  plan_id?: string | null;
  title: string;
  description?: string | null;
  due_at?: string | null;
  priority?: TaskPriority;
  timezone?: string;
};

export type UpdateTaskInput = Partial<
  Pick<
    Task,
    | 'title'
    | 'description'
    | 'status'
    | 'due_at'
    | 'priority'
    | 'timezone'
    | 'plan_id'
  >
> & { expected_revision?: number };

export type CreateReminderInput = {
  plan_id?: string | null;
  title: string;
  body?: string | null;
  trigger_at: string;
  timezone: string;
  recurrence_rule?: string | null;
  task_id?: string | null;
  delivery_channel?: 'push';
};

export type UpdateReminderInput = Partial<
  Pick<
    Reminder,
    | 'title'
    | 'body'
    | 'trigger_at'
    | 'timezone'
    | 'recurrence_rule'
    | 'status'
    | 'task_id'
    | 'plan_id'
  >
> & { expected_revision?: number };
