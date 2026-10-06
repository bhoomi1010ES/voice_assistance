import React, { useEffect, useState } from 'react';
import { StyleSheet, View } from 'react-native';
import { useAuth } from '../../auth/AuthProvider';
import { listPlans } from '../../plans/api';
import { Plan } from '../../plans/types';
import { useVoiceSocket } from '../../voice/VoiceSocketProvider';
import { strings } from '../../i18n/strings';
import { ActionButton, AppText, Card } from '../ui/Primitives';
import { useAppTheme } from '../../design/ThemeProvider';
import { radii, spacing, typography } from '../../design/tokens';

export type PlanningControlsProps = {
  onOpenPlanDetail?: (planId: string) => void;
};

export function PlanningControls({ onOpenPlanDetail }: PlanningControlsProps) {
  const { controller } = useAuth();
  const { colors } = useAppTheme();
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

  const activePlan = plans.find(p => p.id === planning?.activePlanId) ?? null;

  return (
    <Card testID="planning-controls" style={styles.card}>
      {/* Header and Persistent Plan Mode Indicator */}
      <View style={styles.headerRow}>
        <View style={styles.titleGroup}>
          <AppText style={styles.sectionTitle}>{strings.planning.plan}</AppText>
          {planning?.mode === 'plan' ? (
            <View
              style={[styles.badge, { backgroundColor: colors.primaryContainer }]}
              testID="planning-mode-on-badge"
            >
              <AppText style={[styles.badgeText, { color: colors.primary }]}>
                {strings.planning.on}
              </AppText>
            </View>
          ) : null}
        </View>

        <AppText
          style={[styles.statusText, { color: colors.textSubtle }]}
          accessibilityLiveRegion="polite"
        >
          {!available
            ? strings.planning.unavailable
            : planning?.mode === 'plan'
            ? strings.planning.on
            : strings.planning.normal}
        </AppText>
      </View>

      {/* Mode Selector Buttons */}
      <View style={styles.modeButtonGroup}>
        <ActionButton
          label={strings.planning.normal}
          accessibilityLabel={strings.planning.normal}
          accessibilityState={{
            selected: planning?.mode === 'normal',
            disabled,
          }}
          disabled={disabled || planning?.mode === 'normal'}
          variant={planning?.mode === 'normal' ? 'primary' : 'secondary'}
          onPress={() => socket.setPlanningMode('normal')}
          testID="mode-btn-normal"
        />
        <ActionButton
          label={strings.planning.plan}
          accessibilityLabel={strings.planning.plan}
          accessibilityState={{ selected: planning?.mode === 'plan', disabled }}
          disabled={disabled || planning?.mode === 'plan'}
          variant={planning?.mode === 'plan' ? 'primary' : 'secondary'}
          onPress={() => socket.setPlanningMode('plan')}
          testID="mode-btn-plan"
        />
      </View>

      {/* Feedback Messages */}
      {planningPending ? (
        <AppText style={[styles.feedbackText, { color: colors.primary }]}>
          {strings.planning.pending}
        </AppText>
      ) : null}

      {planningError ? (
        <AppText accessibilityRole="alert" style={[styles.feedbackText, { color: colors.error }]}>
          {planningError}
        </AppText>
      ) : null}

      {/* Explanation of automatic tasks & reminders */}
      {planning?.mode === 'plan' ? (
        <View style={[styles.explanationBox, { backgroundColor: colors.surfaceMuted }]}>
          <AppText style={[styles.explanationText, { color: colors.textSubtle }]}>
            {strings.planning.planModeExplanation}
          </AppText>
        </View>
      ) : null}

      {/* Active Plan Detail & Plan Switcher */}
      {available && planning?.mode === 'plan' ? (
        <View style={styles.planSection}>
          <View style={styles.activePlanCard}>
            <View style={styles.activePlanHeader}>
              <View>
                <AppText style={[styles.activePlanLabel, { color: colors.textSubtle }]}>
                  {strings.planning.activePlan}
                </AppText>
                <AppText style={styles.activePlanName} testID="active-plan-name">
                  {planning.activePlanName ?? strings.planning.noPlan}
                </AppText>
                {activePlan?.goal ? (
                  <AppText style={[styles.activePlanGoal, { color: colors.textSubtle }]}>
                    {activePlan.goal}
                  </AppText>
                ) : null}
              </View>

              {planning.activePlanId && onOpenPlanDetail ? (
                <ActionButton
                  label={strings.planning.viewPlanDetails}
                  variant="secondary"
                  onPress={() => onOpenPlanDetail(planning.activePlanId!)}
                  testID="view-plan-details-btn"
                />
              ) : null}
            </View>
          </View>

          {/* Plan Selector List */}
          <View style={styles.planPickerHeader}>
            <AppText style={[styles.planPickerTitle, { color: colors.textSubtle }]}>
              {strings.planning.selectPlan}
            </AppText>
            <ActionButton
              label={strings.planning.refreshPlans}
              variant="quiet"
              disabled={loading || disabled}
              onPress={() => setRefresh(value => value + 1)}
            />
          </View>

          {loading ? (
            <AppText style={[styles.feedbackText, { color: colors.textSubtle }]}>
              {strings.planning.loading}
            </AppText>
          ) : null}

          {loadError ? (
            <AppText accessibilityRole="alert" style={[styles.feedbackText, { color: colors.error }]}>
              {strings.planning.loadError}
            </AppText>
          ) : null}

          <View style={styles.planChipsRow}>
            <ActionButton
              label={strings.planning.noPlan}
              variant="quiet"
              disabled={disabled || !planning.activePlanId}
              onPress={() => socket.selectPlan(null)}
              testID="clear-active-plan-btn"
            />
            {plans.map(plan => (
              <ActionButton
                key={plan.id}
                label={plan.name}
                accessibilityLabel={`${strings.planning.selectPlan}: ${plan.name}`}
                accessibilityState={{
                  selected: planning.activePlanId === plan.id,
                }}
                variant={planning.activePlanId === plan.id ? 'primary' : 'quiet'}
                disabled={disabled || planning.activePlanId === plan.id}
                onPress={() => socket.selectPlan(plan.id)}
                testID={`select-plan-${plan.id}`}
              />
            ))}
          </View>
        </View>
      ) : null}
    </Card>
  );
}

const styles = StyleSheet.create({
  card: {
    gap: spacing.sm,
    padding: spacing.md,
  },
  headerRow: {
    alignItems: 'center',
    flexDirection: 'row',
    justifyContent: 'space-between',
  },
  titleGroup: {
    alignItems: 'center',
    flexDirection: 'row',
    gap: spacing.xs,
  },
  sectionTitle: {
    fontSize: typography.heading,
    fontWeight: '700',
  },
  badge: {
    borderRadius: radii.sm,
    paddingHorizontal: spacing.xs,
    paddingVertical: 2,
  },
  badgeText: {
    fontSize: typography.caption,
    fontWeight: '600',
  },
  statusText: {
    fontSize: typography.body,
  },
  modeButtonGroup: {
    flexDirection: 'row',
    gap: spacing.sm,
  },
  feedbackText: {
    fontSize: typography.caption,
  },
  explanationBox: {
    borderRadius: radii.md,
    padding: spacing.sm,
  },
  explanationText: {
    fontSize: typography.caption,
    lineHeight: 18,
  },
  planSection: {
    gap: spacing.sm,
    marginTop: spacing.xs,
  },
  activePlanCard: {
    borderRadius: radii.md,
    borderWidth: 1,
    borderColor: 'rgba(0,0,0,0.08)',
    padding: spacing.sm,
  },
  activePlanHeader: {
    alignItems: 'center',
    flexDirection: 'row',
    justifyContent: 'space-between',
  },
  activePlanLabel: {
    fontSize: typography.caption,
    fontWeight: '500',
  },
  activePlanName: {
    fontSize: typography.label,
    fontWeight: '700',
    marginTop: 2,
  },
  activePlanGoal: {
    fontSize: typography.caption,
    marginTop: 2,
  },
  planPickerHeader: {
    alignItems: 'center',
    flexDirection: 'row',
    justifyContent: 'space-between',
    marginTop: spacing.xs,
  },
  planPickerTitle: {
    fontSize: typography.caption,
    fontWeight: '600',
  },
  planChipsRow: {
    flexDirection: 'row',
    flexWrap: 'wrap',
    gap: spacing.xs,
  },
});
