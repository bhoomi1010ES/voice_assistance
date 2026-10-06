import React, { useEffect, useState } from 'react';
import { View } from 'react-native';
import { useAuth } from '../../auth/AuthProvider';
import { listPlans } from '../../plans/api';
import { Plan } from '../../plans/types';
import { useVoiceSocket } from '../../voice/VoiceSocketProvider';
import { strings } from '../../i18n/strings';
import { ActionButton, AppText, Card } from '../ui/Primitives';

export function PlanningControls() {
  const { controller } = useAuth();
  const {
    socket,
    planning,
    planningPending,
    planningError,
    connection,
    session,
  } = useVoiceSocket();
  const [plans, setPlans] = useState<Plan[]>([]);
  const [loadError, setLoadError] = useState(false);
  const [loading, setLoading] = useState(false);
  const [refresh, setRefresh] = useState(0);
  const available = Boolean(
    planning?.available && connection === 'connected' && session === 'ready',
  );

  useEffect(() => {
    if (!available || planning?.mode !== 'plan') {
      setPlans([]);
      return;
    }
    let cancelled = false;
    setLoading(true);
    setLoadError(false);
    listPlans(controller)
      .then(items => {
        if (!cancelled) {
          setPlans(items);
        }
      })
      .catch(() => {
        if (!cancelled) {
          setLoadError(true);
        }
      })
      .finally(() => {
        if (!cancelled) {
          setLoading(false);
        }
      });
    return () => {
      cancelled = true;
    };
  }, [available, controller, planning?.mode, refresh]);

  const disabled = !available || Boolean(planningPending);
  return (
    <Card testID="planning-controls">
      <AppText accessibilityLiveRegion="polite">
        {!available
          ? strings.planning.unavailable
          : planning?.mode === 'plan'
          ? strings.planning.on
          : strings.planning.normal}
      </AppText>
      <View>
        <ActionButton
          label={strings.planning.normal}
          accessibilityLabel={strings.planning.normal}
          accessibilityState={{
            selected: planning?.mode === 'normal',
            disabled,
          }}
          disabled={disabled || planning?.mode === 'normal'}
          variant="secondary"
          onPress={() => socket.setPlanningMode('normal')}
        />
        <ActionButton
          label={strings.planning.plan}
          accessibilityLabel={strings.planning.plan}
          accessibilityState={{ selected: planning?.mode === 'plan', disabled }}
          disabled={disabled || planning?.mode === 'plan'}
          variant="secondary"
          onPress={() => socket.setPlanningMode('plan')}
        />
      </View>
      {planningPending ? <AppText>{strings.planning.pending}</AppText> : null}
      {planningError ? (
        <AppText accessibilityRole="alert">{planningError}</AppText>
      ) : null}
      {available && planning?.mode === 'plan' ? (
        <View>
          <AppText>{strings.planning.foundationNotice}</AppText>
          <AppText>
            {planning.activePlanName ?? strings.planning.noPlan}
          </AppText>
          <ActionButton
            label={strings.planning.refreshPlans}
            variant="quiet"
            disabled={loading || disabled}
            onPress={() => setRefresh(value => value + 1)}
          />
          {loading ? <AppText>{strings.planning.loading}</AppText> : null}
          {loadError ? (
            <AppText accessibilityRole="alert">
              {strings.planning.loadError}
            </AppText>
          ) : null}
          <ActionButton
            label={strings.planning.noPlan}
            variant="quiet"
            disabled={disabled || !planning.activePlanId}
            onPress={() => socket.selectPlan(null)}
          />
          {plans.map(plan => (
            <ActionButton
              key={plan.id}
              label={plan.name}
              accessibilityLabel={`${strings.planning.selectPlan}: ${plan.name}`}
              accessibilityState={{
                selected: planning.activePlanId === plan.id,
              }}
              variant="quiet"
              disabled={disabled || planning.activePlanId === plan.id}
              onPress={() => socket.selectPlan(plan.id)}
            />
          ))}
        </View>
      ) : null}
    </Card>
  );
}
