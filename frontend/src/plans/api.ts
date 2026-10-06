import { AuthController } from '../auth/AuthController';
import { Plan, PlanDetail, PlanningReceiptPayload } from './types';
import { Task } from '../tasks/types';

export function listPlans(
  controller: AuthController,
  status: 'active' | 'completed' | 'archived' = 'active',
): Promise<Plan[]> {
  return controller.request(`/plans?status=${status}&limit=100`);
}

export function createPlan(
  controller: AuthController,
  input: { name: string; goal?: string; deadline_at?: string; timezone?: string },
): Promise<Plan> {
  return controller.request('/plans', {
    method: 'POST',
    body: JSON.stringify(input),
  });
}

export function getPlan(controller: AuthController, planId: string): Promise<PlanDetail> {
  return controller.request(`/plans/${encodeURIComponent(planId)}`);
}

export function updatePlan(
  controller: AuthController,
  planId: string,
  input: {
    expected_revision: number;
    name?: string;
    goal?: string | null;
    status?: 'active' | 'completed' | 'archived';
    deadline_at?: string | null;
  },
): Promise<Plan> {
  return controller.request(`/plans/${encodeURIComponent(planId)}`, {
    method: 'PATCH',
    body: JSON.stringify(input),
  });
}

export function archivePlan(
  controller: AuthController,
  planId: string,
  expectedRevision: number,
): Promise<Plan> {
  return updatePlan(controller, planId, {
    expected_revision: expectedRevision,
    status: 'archived',
  });
}

export function listPlanActions(
  controller: AuthController,
  planId: string,
): Promise<PlanningReceiptPayload[]> {
  return controller.request(`/plans/${encodeURIComponent(planId)}/actions?limit=50`);
}

export function listPlanTasks(
  controller: AuthController,
  planId: string,
): Promise<Task[]> {
  return controller.request(`/tasks?plan_id=${encodeURIComponent(planId)}&limit=100`);
}
