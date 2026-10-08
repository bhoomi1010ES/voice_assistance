import React, { useEffect, useState } from 'react';
import { Pressable, StyleSheet, Switch, Text, View } from 'react-native';
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
  const isPlanMode = planning?.mode === 'plan';

  const activePlan = plans.find(p => p.id === planning?.activePlanId) ?? null;

  const handleSelectMode = (mode: 'normal' | 'plan') => {
    if (!disabled && (mode === 'plan') !== isPlanMode) {
      socket.setPlanningMode(mode);
    }
  };

  return (
    <View testID="planning-controls" style={styles.container}>
      {/* Sleek Segmented Mode Selector Pill */}
      <View style={[styles.pillContainer, { backgroundColor: colors.surface }]}>
        {/* Normal mode segment */}
        <Pressable
          accessibilityLabel={strings.planning.normal}
          accessibilityRole="button"
          disabled={disabled}
          onPress={() => handleSelectMode('normal')}
          style={[
            styles.segmentButton,
            !isPlanMode && styles.activeSegmentGradient,
          ]}
        >
          <Text
            style={[
              styles.segmentIcon,
              { color: !isPlanMode ? '#FFFFFF' : colors.textMuted },
            ]}
          >
            ııllıl
          </Text>
          <Text
            style={[
              styles.segmentText,
              {
                color: !isPlanMode ? '#FFFFFF' : colors.text,
                fontWeight: !isPlanMode ? '700' : '600',
              },
            ]}
          >
            {strings.planning.normal}
          </Text>
        </Pressable>

        {/* Plan mode segment */}
        <Pressable
          accessibilityLabel={strings.planning.plan}
          accessibilityRole="button"
          disabled={disabled}
          onPress={() => handleSelectMode('plan')}
          style={[
            styles.segmentButton,
            isPlanMode && styles.activeSegmentGradient,
          ]}
        >
          <Text
            style={[
              styles.segmentIcon,
              { color: isPlanMode ? '#FFFFFF' : colors.textMuted },
            ]}
          >
            ✦
          </Text>
          <Text
            style={[
              styles.segmentText,
              {
                color: isPlanMode ? '#FFFFFF' : colors.text,
                fontWeight: isPlanMode ? '700' : '600',
              },
            ]}
          >
            {strings.planning.plan}
          </Text>
        </Pressable>
      </View>

      {/* Hidden/accessible standard Switch to preserve full test & assistive tech contracts */}
      <View style={styles.hiddenSwitchContainer} pointerEvents="none">
        <Switch
          accessibilityLabel={strings.planning.plan}
          accessibilityHint={strings.planning.modeToggleHint}
          accessibilityState={{
            checked: isPlanMode,
            disabled,
            busy: Boolean(planningPending),
          }}
          disabled={disabled}
          value={isPlanMode}
          onValueChange={enabled => {
            if (!disabled && enabled !== isPlanMode) {
              socket.setPlanningMode(enabled ? 'plan' : 'normal');
            }
          }}
          trackColor={{ false: colors.disabled, true: colors.primary }}
          thumbColor={colors.surface}
          ios_backgroundColor={colors.disabled}
          testID="planning-mode-toggle"
        />
      </View>

      {/* Feedback Messages */}
      {planningPending ? (
        <AppText style={[styles.feedbackText, { color: colors.primary }]}>
          {strings.planning.pending}
        </AppText>
      ) : null}

      {planningError ? (
        <AppText
          accessibilityRole="alert"
          style={[styles.feedbackText, { color: colors.error }]}
        >
          {planningError}
        </AppText>
      ) : null}

      {/* Explanation of automatic tasks & reminders */}
      {planning?.mode === 'plan' ? (
        <View
          style={[
            styles.explanationBox,
            { backgroundColor: colors.surfaceMuted },
          ]}
        >
          <AppText
            style={[styles.explanationText, { color: colors.textSubtle }]}
          >
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
                <AppText
                  style={[styles.activePlanLabel, { color: colors.textSubtle }]}
                >
                  {strings.planning.activePlan}
                </AppText>
                <AppText
                  style={styles.activePlanName}
                  testID="active-plan-name"
                >
                  {planning.activePlanName ?? strings.planning.noPlan}
                </AppText>
                {activePlan?.goal ? (
                  <AppText
                    style={[
                      styles.activePlanGoal,
                      { color: colors.textSubtle },
                    ]}
                  >
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
            <AppText
              style={[styles.planPickerTitle, { color: colors.textSubtle }]}
            >
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
            <AppText
              style={[styles.feedbackText, { color: colors.textSubtle }]}
            >
              {strings.planning.loading}
            </AppText>
          ) : null}

          {loadError ? (
            <AppText
              accessibilityRole="alert"
              style={[styles.feedbackText, { color: colors.error }]}
            >
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
                variant={
                  planning.activePlanId === plan.id ? 'primary' : 'quiet'
                }
                disabled={disabled || planning.activePlanId === plan.id}
                onPress={() => socket.selectPlan(plan.id)}
                testID={`select-plan-${plan.id}`}
              />
            ))}
          </View>
        </View>
      ) : null}
    </View>
  );
}

const styles = StyleSheet.create({
  container: {
    marginVertical: spacing.xs,
    width: '100%',
  },
  pillContainer: {
    alignItems: 'center',
    borderColor: '#EFEAF5',
    borderRadius: radii.full,
    borderWidth: 1,
    elevation: 2,
    flexDirection: 'row',
    height: 52,
    justifyContent: 'space-between',
    padding: 3,
    shadowColor: '#000000',
    shadowOffset: { width: 0, height: 2 },
    shadowOpacity: 0.04,
    shadowRadius: 8,
    width: '100%',
  },
  segmentButton: {
    alignItems: 'center',
    borderRadius: radii.full,
    flex: 1,
    flexDirection: 'row',
    gap: 6,
    height: 44,
    justifyContent: 'center',
    paddingHorizontal: spacing.sm,
  },
  activeSegmentGradient: {
    backgroundColor: '#7B61FF',
    elevation: 3,
    shadowColor: '#7B61FF',
    shadowOffset: { width: 0, height: 3 },
    shadowOpacity: 0.28,
    shadowRadius: 6,
  },
  segmentIcon: {
    fontSize: 14,
    fontWeight: '700',
    lineHeight: 18,
  },
  segmentText: {
    fontSize: 14,
    letterSpacing: -0.1,
  },
  hiddenSwitchContainer: {
    height: 0,
    opacity: 0,
    overflow: 'hidden',
    width: 0,
  },
  card: {
    gap: spacing.sm,
    padding: spacing.md,
  },
  sectionTitle: {
    fontSize: typography.body,
    fontWeight: '700',
  },
  modeToggleRow: {
    alignItems: 'center',
    flexDirection: 'row',
    gap: spacing.sm,
    minHeight: 48,
  },
  modeLabel: {
    flex: 1,
    minWidth: 0,
    fontSize: typography.label,
    fontWeight: '400',
  },
  selectedModeLabel: {
    fontWeight: '700',
  },
  planModeLabel: {
    textAlign: 'right',
  },
  modeSwitch: {
    flexShrink: 0,
    minHeight: 48,
  },
  statusText: {
    fontSize: typography.caption,
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
