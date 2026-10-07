export type PlanningState = {
  mode: 'normal' | 'plan';
  activePlanId: string | null;
  activePlanName: string | null;
  planRevision: number | null;
  stateVersion: number;
  policyVersion: string;
  available: boolean;
  automaticActionsAvailable: boolean;
};

export type Plan = {
  id: string;
  name: string;
  goal: string | null;
  status: 'active' | 'completed' | 'archived';
  deadline_at: string | null;
  timezone: string;
  revision: number;
  created_at: string;
  updated_at: string;
};

export type PlanContextItem = {
  id: string;
  plan_id: string;
  kind: string;
  content: string;
  value_json?: Record<string, unknown> | null;
  status: 'active' | 'archived';
  revision: number;
  created_at: string;
  updated_at: string;
};

export type PlanDetail = {
  plan: Plan;
  context: PlanContextItem[];
  task_counts: Record<string, number>;
};

export type PlanningActionReceipt = {
  id: string;
  actionId?: string | null;
  action?: string;
  operation?: string;
  title: string;
  status: 'saved' | 'updated' | 'duplicate' | 'skipped' | 'failed';
  targetId?: string | null;
  scheduledAt?: string | null;
  planId?: string | null;
  error?: string | null;
  result?: Record<string, unknown> | null;
};

export type PlanningReceiptPayload = {
  batchId?: string | null;
  planId?: string | null;
  savedActions: PlanningActionReceipt[];
  duplicateActions: PlanningActionReceipt[];
  failedActions: PlanningActionReceipt[];
  summary?: string | null;
  textSummary?: string | null;
  timestampMs?: number;
};

export function parsePlanningState(input: unknown): PlanningState | null {
  let value = input;
  if (typeof value === 'string') {
    if (value.length > 4096) {
      return null;
    }
    try {
      value = JSON.parse(value);
    } catch {
      return null;
    }
  }
  if (!value || typeof value !== 'object' || Array.isArray(value)) {
    return null;
  }
  const state = value as Record<string, unknown>;
  const boundedText = (text: unknown, max: number): string | null =>
    typeof text === 'string' && text.length <= max ? text : null;
  if (
    (state.mode !== 'normal' && state.mode !== 'plan') ||
    !Number.isSafeInteger(state.state_version) ||
    Number(state.state_version) < 1 ||
    typeof state.available !== 'boolean'
  ) {
    return null;
  }
  return {
    mode: state.mode,
    stateVersion: Number(state.state_version),
    activePlanId: boundedText(state.active_plan_id, 128),
    activePlanName: boundedText(state.active_plan_name, 255),
    planRevision: Number.isSafeInteger(state.plan_revision)
      ? Number(state.plan_revision)
      : null,
    policyVersion: boundedText(state.policy_version, 64) ?? 'plan-v1',
    available: state.available,
    automaticActionsAvailable: state.automatic_actions_available === true,
  };
}

export function parsePlanningAction(
  input: unknown,
): PlanningActionReceipt | null {
  if (!input || typeof input !== 'object' || Array.isArray(input)) {
    return null;
  }
  const raw = input as Record<string, unknown>;
  const title =
    typeof raw.title === 'string' && raw.title.trim()
      ? raw.title.trim().slice(0, 255)
      : typeof raw.name === 'string' && raw.name.trim()
      ? raw.name.trim().slice(0, 255)
      : 'Action';

  const rawStatus = String(raw.status ?? '').toLowerCase();
  let status: PlanningActionReceipt['status'] = 'saved';
  if (
    rawStatus === 'updated' ||
    raw.action === 'UPDATE_TASK' ||
    raw.operation === 'UPDATE_TASK'
  ) {
    status = 'updated';
  } else if (rawStatus === 'duplicate') {
    status = 'duplicate';
  } else if (rawStatus === 'skipped') {
    status = 'skipped';
  } else if (rawStatus === 'failed' || raw.error) {
    status = 'failed';
  }

  const id =
    typeof raw.id === 'string'
      ? raw.id
      : typeof raw.action_id === 'string'
      ? raw.action_id
      : typeof raw.target_id === 'string'
      ? raw.target_id
      : `act-${Math.random().toString(36).slice(2, 9)}`;

  return {
    id,
    actionId: typeof raw.action_id === 'string' ? raw.action_id : null,
    action:
      typeof raw.action === 'string'
        ? raw.action
        : typeof raw.operation === 'string'
        ? raw.operation
        : undefined,
    operation:
      typeof raw.operation === 'string'
        ? raw.operation
        : typeof raw.action === 'string'
        ? raw.action
        : undefined,
    title,
    status,
    targetId:
      typeof raw.target_id === 'string'
        ? raw.target_id
        : typeof raw.id === 'string'
        ? raw.id
        : null,
    scheduledAt: typeof raw.scheduled_at === 'string' ? raw.scheduled_at : null,
    planId: typeof raw.plan_id === 'string' ? raw.plan_id : null,
    error: typeof raw.error === 'string' ? raw.error : null,
    result:
      raw.result && typeof raw.result === 'object' && !Array.isArray(raw.result)
        ? (raw.result as Record<string, unknown>)
        : null,
  };
}

export function parsePlanningReceipt(
  input: unknown,
): PlanningReceiptPayload | null {
  let value = input;
  if (typeof value === 'string') {
    if (value.length > 32768) {
      return null;
    }
    try {
      value = JSON.parse(value);
    } catch {
      return null;
    }
  }
  if (!value || typeof value !== 'object') {
    return null;
  }
  const raw = value as Record<string, unknown>;

  const toList = (
    items: unknown,
    defaultStatus: PlanningActionReceipt['status'],
  ): PlanningActionReceipt[] => {
    if (!Array.isArray(items)) return [];
    const results: PlanningActionReceipt[] = [];
    for (const item of items) {
      const parsed = parsePlanningAction(item);
      if (parsed) {
        if (!item.status && defaultStatus) {
          parsed.status = defaultStatus;
        }
        results.push(parsed);
      }
    }
    return results;
  };

  const savedActions = toList(
    raw.saved_actions ?? raw.savedActions ?? raw.actions,
    'saved',
  );
  const duplicateActions = toList(
    raw.duplicate_actions ?? raw.duplicateActions,
    'duplicate',
  );
  const failedActions = toList(
    raw.failed_actions ?? raw.failedActions,
    'failed',
  );

  if (
    savedActions.length === 0 &&
    duplicateActions.length === 0 &&
    failedActions.length === 0
  ) {
    if (Array.isArray(raw)) {
      const list = toList(raw, 'saved');
      if (list.length > 0) {
        return {
          savedActions: list,
          duplicateActions: [],
          failedActions: [],
        };
      }
    }
    return null;
  }

  return {
    batchId:
      typeof raw.batch_id === 'string'
        ? raw.batch_id
        : typeof raw.batchId === 'string'
        ? raw.batchId
        : null,
    planId:
      typeof raw.plan_id === 'string'
        ? raw.plan_id
        : typeof raw.planId === 'string'
        ? raw.planId
        : null,
    savedActions,
    duplicateActions,
    failedActions,
    summary: typeof raw.summary === 'string' ? raw.summary : null,
    textSummary:
      typeof raw.text_summary === 'string'
        ? raw.text_summary
        : typeof raw.textSummary === 'string'
        ? raw.textSummary
        : null,
    timestampMs:
      typeof raw.timestamp_ms === 'number' ? raw.timestamp_ms : Date.now(),
  };
}
