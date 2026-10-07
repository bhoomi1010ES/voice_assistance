import React from 'react';
import { Alert } from 'react-native';
import ReactTestRenderer, { act } from 'react-test-renderer';
import { PlanningReceiptCard } from '../src/components/voice/PlanningReceiptCard';
import { PlanDetailModal } from '../src/components/plans/PlanDetailModal';
import { ActionButton, AppText } from '../src/components/ui/Primitives';
import { TestProviders } from '../src/testing/TestProviders';
import { PlanningReceiptPayload } from '../src/plans/types';
import { getPlan, listPlanTasks, archivePlan } from '../src/plans/api';
import { completeTask } from '../src/tasks/api';

jest.mock('../src/auth/AuthProvider', () => {
  const controller = {};
  return { useAuth: () => ({ controller }) };
});

jest.mock('../src/plans/api', () => ({
  getPlan: jest.fn(),
  listPlanTasks: jest.fn(),
  archivePlan: jest.fn(),
}));

jest.mock('../src/tasks/api', () => ({
  completeTask: jest.fn(),
  deleteTask: jest.fn(),
}));

let renderer: ReactTestRenderer.ReactTestRenderer;

beforeEach(() => {
  jest.clearAllMocks();
  jest.spyOn(Alert, 'alert').mockImplementation((_title, _msg, buttons) => {
    const actionBtn = buttons?.find(
      b =>
        b.style === 'destructive' || b.text === 'Archive' || b.text === 'Undo',
    );
    if (actionBtn && actionBtn.onPress) {
      actionBtn.onPress();
    }
  });
});

afterEach(() => {
  act(() => renderer?.unmount());
  jest.restoreAllMocks();
});

describe('PlanningReceiptCard', () => {
  const receipt: PlanningReceiptPayload = {
    batchId: 'batch-123',
    planId: 'plan-abc',
    textSummary: 'Planned 2 actions for moving trip',
    savedActions: [
      {
        id: 'act-1',
        title: 'Pack fragile boxes',
        action: 'CREATE_TASK',
        status: 'saved',
        scheduledAt: '2026-10-10 10:00 AM',
      },
      {
        id: 'act-2',
        title: 'Update existing move date',
        action: 'UPDATE_TASK',
        status: 'updated',
      },
    ],
    duplicateActions: [
      {
        id: 'act-3',
        title: 'Pack boxes duplicate',
        action: 'CREATE_TASK',
        status: 'duplicate',
      },
    ],
    failedActions: [],
  };

  test('renders action items, summaries, status badges and handles undo/dismiss', async () => {
    const onDismiss = jest.fn();
    const onUndoAction = jest.fn();
    const onEditAction = jest.fn();

    await act(async () => {
      renderer = ReactTestRenderer.create(
        <TestProviders>
          <PlanningReceiptCard
            receipt={receipt}
            onDismiss={onDismiss}
            onUndoAction={onUndoAction}
            onEditAction={onEditAction}
          />
        </TestProviders>,
      );
    });

    const texts = renderer.root
      .findAllByType(AppText)
      .map(t => t.props.children);
    expect(texts).toContain('Planned 2 actions for moving trip');
    expect(texts).toContain('Pack fragile boxes');
    expect(texts).toContain('Update existing move date');
    expect(texts).toContain('Pack boxes duplicate');

    // Dismiss button
    const dismissBtn = renderer.root
      .findAllByType(ActionButton)
      .find(b => b.props.testID === 'dismiss-receipt-btn');
    expect(dismissBtn).toBeDefined();
    act(() => dismissBtn!.props.onPress());
    expect(onDismiss).toHaveBeenCalledTimes(1);

    // Edit button for saved action
    const editBtn = renderer.root
      .findAllByType(ActionButton)
      .find(b => b.props.testID === 'edit-action-act-1');
    expect(editBtn).toBeDefined();
    act(() => editBtn!.props.onPress());
    expect(onEditAction).toHaveBeenCalledWith(receipt.savedActions[0]);

    // Undo button for updated action (triggers confirmation alert)
    const undoBtn = renderer.root
      .findAllByType(ActionButton)
      .find(b => b.props.testID === 'undo-action-act-2');
    expect(undoBtn).toBeDefined();
    await act(async () => {
      undoBtn!.props.onPress();
    });
    expect(onUndoAction).toHaveBeenCalledWith(receipt.savedActions[1]);
  });
});

describe('PlanDetailModal', () => {
  const planDetail = {
    plan: {
      id: 'plan-1',
      name: 'Apartment Relocation',
      goal: 'Move to new apartment by end of month',
      status: 'active' as const,
      deadline_at: '2026-10-31T00:00:00Z',
      timezone: 'UTC',
      revision: 1,
      created_at: '2026-10-01T00:00:00Z',
      updated_at: '2026-10-01T00:00:00Z',
    },
    context: [
      {
        id: 'ctx-1',
        plan_id: 'plan-1',
        kind: 'preference',
        content: 'Prefers morning delivery',
        status: 'active' as const,
        revision: 1,
        created_at: '2026-10-01T00:00:00Z',
        updated_at: '2026-10-01T00:00:00Z',
      },
    ],
    task_counts: { total: 3, completed: 1 },
  };

  const planTasks = [
    {
      id: 'task-1',
      title: 'Finish packing',
      status: 'pending' as const,
      due_at: '2026-10-06T12:00:00Z', // Today
      created_at: '2026-10-01T00:00:00Z',
      updated_at: '2026-10-01T00:00:00Z',
    },
    {
      id: 'task-2',
      title: 'Return keys',
      status: 'completed' as const,
      due_at: '2026-10-05T12:00:00Z',
      created_at: '2026-10-01T00:00:00Z',
      updated_at: '2026-10-01T00:00:00Z',
    },
    {
      id: 'task-3',
      title: 'Setup utilities',
      status: 'pending' as const,
      due_at: null, // No deadline
      created_at: '2026-10-01T00:00:00Z',
      updated_at: '2026-10-01T00:00:00Z',
    },
  ];

  beforeEach(() => {
    (getPlan as jest.Mock).mockResolvedValue(planDetail);
    (listPlanTasks as jest.Mock).mockResolvedValue(planTasks);
  });

  test('renders plan details, goal, context, and groups tasks with completion toggles', async () => {
    const onClose = jest.fn();

    await act(async () => {
      renderer = ReactTestRenderer.create(
        <TestProviders>
          <PlanDetailModal planId="plan-1" visible={true} onClose={onClose} />
        </TestProviders>,
      );
    });

    // Wait for async loadData effect
    await act(async () => {
      await Promise.resolve();
    });

    const texts = renderer.root
      .findAllByType(AppText)
      .map(t => t.props.children);
    expect(texts).toContain('Apartment Relocation');
    expect(texts).toContain('Move to new apartment by end of month');
    expect(texts).toContain('Prefers morning delivery');
    expect(texts).toContain('Finish packing');
    expect(texts).toContain('Return keys');
    expect(texts).toContain('Setup utilities');

    // Toggle task completion
    const checkBtn = renderer.root
      .findAllByType(ActionButton)
      .find(b => b.props.testID === 'complete-task-task-1');
    expect(checkBtn).toBeDefined();

    (completeTask as jest.Mock).mockResolvedValue({
      ...planTasks[0],
      status: 'completed',
    });

    await act(async () => {
      await checkBtn!.props.onPress();
    });

    expect(completeTask).toHaveBeenCalledWith(expect.anything(), 'task-1');
  });

  test('confirmed plan archiving triggers archivePlan and notifies parent', async () => {
    const onClose = jest.fn();
    const onPlanArchived = jest.fn();

    await act(async () => {
      renderer = ReactTestRenderer.create(
        <TestProviders>
          <PlanDetailModal
            planId="plan-1"
            visible={true}
            onClose={onClose}
            onPlanArchived={onPlanArchived}
          />
        </TestProviders>,
      );
    });

    await act(async () => {
      await Promise.resolve();
    });

    const archiveBtn = renderer.root
      .findAllByType(ActionButton)
      .find(b => b.props.testID === 'archive-plan-btn');
    expect(archiveBtn).toBeDefined();

    (archivePlan as jest.Mock).mockResolvedValue({
      ...planDetail.plan,
      status: 'archived',
    });

    await act(async () => {
      await archiveBtn!.props.onPress();
    });

    expect(archivePlan).toHaveBeenCalledWith(expect.anything(), 'plan-1', 1);
    expect(onPlanArchived).toHaveBeenCalledWith('plan-1');
    expect(onClose).toHaveBeenCalled();
  });
});
