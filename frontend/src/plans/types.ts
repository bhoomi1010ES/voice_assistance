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
