import React from 'react';
import ReactTestRenderer, { act } from 'react-test-renderer';
import { Switch } from 'react-native';
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

test('toggle requests both modes and stays on the server mode until acknowledgement', async () => {
  await act(async () => {
    renderer = ReactTestRenderer.create(
      <TestProviders>
        <PlanningControls />
      </TestProviders>,
    );
  });
  const toggle = () => renderer.root.findByType(Switch);
  expect(toggle().props.value).toBe(false);
  act(() => toggle().props.onValueChange(true));
  expect(setPlanningMode).toHaveBeenCalledWith('plan');
  expect(toggle().props.value).toBe(false);
  expect(toggle().props.accessibilityState.checked).toBe(false);
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
  expect(toggle().props.disabled).toBe(true);
  expect(toggle().props.accessibilityState.busy).toBe(true);
  act(() => toggle().props.onValueChange(true));
  expect(setPlanningMode).toHaveBeenCalledTimes(1);
  (useVoiceSocket as jest.Mock).mockReturnValue({
    ...snapshot,
    planning: { ...snapshot.planning, mode: 'plan' },
  });
  await act(async () =>
    renderer.update(
      <TestProviders>
        <PlanningControls />
      </TestProviders>,
    ),
  );
  expect(toggle().props.value).toBe(true);
  expect(toggle().props.accessibilityState.checked).toBe(true);
  expect(toggle().props.disabled).toBe(false);
  act(() => toggle().props.onValueChange(false));
  expect(setPlanningMode).toHaveBeenLastCalledWith('normal');
  expect(toggle().props.value).toBe(true);
  (useVoiceSocket as jest.Mock).mockReturnValue(snapshot);
  act(() =>
    renderer.update(
      <TestProviders>
        <PlanningControls />
      </TestProviders>,
    ),
  );
  expect(toggle().props.value).toBe(false);
});

test.each([
  ['unavailable', { planning: { ...snapshot.planning, available: false } }],
  ['reconnecting', { connection: 'reconnecting' }],
  ['session not ready', { session: 'starting' }],
  ['no server snapshot', { planning: null }],
])('toggle cannot change mode when %s', async (_, state) => {
  (useVoiceSocket as jest.Mock).mockReturnValue({ ...snapshot, ...state });
  await act(async () => {
    renderer = ReactTestRenderer.create(
      <TestProviders>
        <PlanningControls />
      </TestProviders>,
    );
  });
  const toggle = renderer.root.findByType(Switch);
  expect(toggle.props.disabled).toBe(true);
  expect(toggle.props.accessibilityState.disabled).toBe(true);
  act(() => toggle.props.onValueChange(true));
  expect(setPlanningMode).not.toHaveBeenCalled();
  expect(listPlans).not.toHaveBeenCalled();
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
