import { AuthController } from '../auth/AuthController';
import { Plan } from './types';

export function listPlans(controller: AuthController): Promise<Plan[]> {
  return controller.request('/plans?status=active&limit=100');
}

export function createPlan(
  controller: AuthController,
  input: { name: string; goal?: string; timezone?: string },
): Promise<Plan> {
  return controller.request('/plans', {
    method: 'POST',
    body: JSON.stringify(input),
  });
}
