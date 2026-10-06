import React from 'react';
import ReactTestRenderer, { act } from 'react-test-renderer';
import { PlanningControls } from '../src/components/voice/PlanningControls';
import { ActionButton } from '../src/components/ui/Primitives';
import { TestProviders } from '../src/testing/TestProviders';
import { useVoiceSocket } from '../src/voice/VoiceSocketProvider';
import { listPlans } from '../src/plans/api';

jest.mock('../src/auth/AuthProvider', () => {
  const controller = {};
  return { useAuth: () => ({ controller }) };
});
jest.mock('../src/voice/VoiceSocketProvider', () => ({
  useVoiceSocket: jest.fn(),
}));
jest.mock('../src/plans/api', () => ({ listPlans: jest.fn() }));

const setPlanningMode = jest.fn();
const selectPlan = jest.fn();
let renderer: ReactTestRenderer.ReactTestRenderer;
const snapshot = {
  socket: { setPlanningMode, selectPlan },
  connection: 'connected',
  session: 'ready',
  planning: {
    mode: 'normal',
    available: true,
    stateVersion: 1,
    activePlanId: null,
    activePlanName: null,
    automaticActionsAvailable: false,
  },
  planningPending: false,
};

beforeEach(() => {
  jest.clearAllMocks();
  (useVoiceSocket as jest.Mock).mockReturnValue(snapshot);
  (listPlans as jest.Mock).mockResolvedValue([{ id: 'plan-1', name: 'XYZ' }]);
});
afterEach(() => act(() => renderer?.unmount()));

test('selector uses server mode and disables both controls until acknowledgement', async () => {
  await act(async () => {
    renderer = ReactTestRenderer.create(
      <TestProviders>
        <PlanningControls />
      </TestProviders>,
    );
  });
  const button = () =>
    renderer.root
      .findAllByType(ActionButton)
      .find(item => item.props.label === 'Plan mode')!;
  act(() => button().props.onPress());
  expect(setPlanningMode).toHaveBeenCalledWith('plan');
  expect(button().props.accessibilityState.selected).toBe(false);
  (useVoiceSocket as jest.Mock).mockReturnValue({
    ...snapshot,
    planningPending: true,
  });
  act(() =>
    renderer.update(
      <TestProviders>
        <PlanningControls />
      </TestProviders>,
    ),
  );
  expect(button().props.disabled).toBe(true);
  (useVoiceSocket as jest.Mock).mockReturnValue({
    ...snapshot,
    connection: 'reconnecting',
    planning: null,
  });
  act(() =>
    renderer.update(
      <TestProviders>
        <PlanningControls />
      </TestProviders>,
    ),
  );
  expect(button().props.disabled).toBe(true);
});

test('active plan is selected through the socket after loading owned plans', async () => {
  (useVoiceSocket as jest.Mock).mockReturnValue({
    ...snapshot,
    planning: { ...snapshot.planning, mode: 'plan' },
  });
  await act(async () => {
    renderer = ReactTestRenderer.create(
      <TestProviders>
        <PlanningControls />
      </TestProviders>,
    );
  });
  const button = renderer.root
    .findAllByType(ActionButton)
    .find(item => item.props.label === 'XYZ')!;
  act(() => button.props.onPress());
  expect(selectPlan).toHaveBeenCalledWith('plan-1');
});
