import React, { useState } from 'react';
import { Alert, StyleSheet, View } from 'react-native';
import { PlanningActionReceipt, PlanningReceiptPayload } from '../../plans/types';
import { strings } from '../../i18n/strings';
import { ActionButton, AppText, Card } from '../ui/Primitives';
import { useAppTheme } from '../../design/ThemeProvider';
import { radii, spacing, typography } from '../../design/tokens';

export type PlanningReceiptCardProps = {
  receipt: PlanningReceiptPayload | null;
  onDismiss?: () => void;
  onUndoAction?: (action: PlanningActionReceipt) => Promise<void>;
  onEditAction?: (action: PlanningActionReceipt) => void;
};

export function PlanningReceiptCard({
  receipt,
  onDismiss,
  onUndoAction,
  onEditAction,
}: PlanningReceiptCardProps) {
  const { colors } = useAppTheme();
  const [busyActionId, setBusyActionId] = useState<string | null>(null);
  const [errorMessage, setErrorMessage] = useState<string | null>(null);

  if (!receipt) {
    return null;
  }

  const allActions = [
    ...receipt.savedActions,
    ...receipt.duplicateActions,
    ...receipt.failedActions,
  ];

  if (allActions.length === 0 && !receipt.summary && !receipt.textSummary) {
    return null;
  }

  const handleUndo = (action: PlanningActionReceipt) => {
    Alert.alert(
      strings.planning.confirmRemovalTitle,
      `${strings.planning.confirmRemovalBody}\n\n"${action.title}"`,
      [
        { text: strings.tasks.cancel, style: 'cancel' },
        {
          text: strings.planning.undoAction,
          style: 'destructive',
          onPress: async () => {
            if (!onUndoAction) return;
            setBusyActionId(action.id);
            setErrorMessage(null);
            try {
              await onUndoAction(action);
            } catch (err: any) {
              setErrorMessage(
                err?.code === 'REVISION_CONFLICT' || err?.message?.includes('conflict')
                  ? strings.planning.staleUndoNotice
                  : strings.tasks.deliveryFailed,
              );
            } finally {
              setBusyActionId(null);
            }
          },
        },
      ],
    );
  };

  const getStatusBadge = (status: PlanningActionReceipt['status']) => {
    switch (status) {
      case 'saved':
        return {
          label: strings.planning.actionSaved,
          bgColor: colors.successContainer,
          textColor: colors.success,
        };
      case 'updated':
        return {
          label: strings.planning.actionUpdated,
          bgColor: colors.primaryContainer,
          textColor: colors.primary,
        };
      case 'duplicate':
        return {
          label: strings.planning.actionDuplicate,
          bgColor: colors.warningContainer,
          textColor: colors.warning,
        };
      case 'skipped':
        return {
          label: strings.planning.actionSkipped,
          bgColor: colors.surfaceMuted,
          textColor: colors.textSubtle,
        };
      case 'failed':
      default:
        return {
          label: strings.planning.actionFailed,
          bgColor: colors.errorContainer,
          textColor: colors.error,
        };
    }
  };

  return (
    <Card testID="planning-receipt-card" style={styles.card}>
      <View style={styles.headerRow}>
        <View style={styles.titleGroup}>
          <AppText style={styles.title}>{strings.planning.receiptTitle}</AppText>
          <View style={[styles.badge, { backgroundColor: colors.primaryContainer }]}>
            <AppText style={[styles.badgeText, { color: colors.primary }]}>
              {allActions.length}
            </AppText>
          </View>
        </View>

        {onDismiss ? (
          <ActionButton
            label="×"
            variant="quiet"
            onPress={onDismiss}
            accessibilityLabel={strings.assistant.cancel || 'Dismiss'}
            testID="dismiss-receipt-btn"
          />
        ) : null}
      </View>

      {receipt.textSummary ?? receipt.summary ? (
        <AppText style={[styles.summaryText, { color: colors.textSubtle }]}>
          {receipt.textSummary ?? receipt.summary}
        </AppText>
      ) : null}

      {errorMessage ? (
        <AppText accessibilityRole="alert" style={[styles.errorText, { color: colors.error }]}>
          {errorMessage}
        </AppText>
      ) : null}

      <View style={styles.actionList}>
        {allActions.map(action => {
          const badge = getStatusBadge(action.status);
          const isBusy = busyActionId === action.id;

          return (
            <View
              key={action.id}
              style={[styles.actionItem, { backgroundColor: colors.surfaceMuted }]}
              testID={`receipt-action-${action.id}`}
            >
              <View style={styles.actionHeader}>
                <View style={styles.actionTitleContainer}>
                  <AppText style={styles.actionTitle}>{action.title}</AppText>
                  {action.scheduledAt ? (
                    <AppText style={[styles.actionTime, { color: colors.textSubtle }]}>
                      {action.scheduledAt}
                    </AppText>
                  ) : null}
                  {action.targetId ? (
                    <AppText style={[styles.targetIdText, { color: colors.textSubtle }]}>
                      ID: {action.targetId.slice(0, 8)}…
                    </AppText>
                  ) : null}
                </View>

                <View style={[styles.statusBadge, { backgroundColor: badge.bgColor }]}>
                  <AppText style={[styles.statusBadgeText, { color: badge.textColor }]}>
                    {badge.label}
                  </AppText>
                </View>
              </View>

              <View style={styles.actionButtonsRow}>
                {onEditAction && action.status === 'saved' ? (
                  <ActionButton
                    label={strings.tasks.edit}
                    variant="quiet"
                    onPress={() => onEditAction(action)}
                    testID={`edit-action-${action.id}`}
                  />
                ) : null}

                {onUndoAction && (action.status === 'saved' || action.status === 'updated') ? (
                  <ActionButton
                    label={strings.planning.undoAction}
                    variant="quiet"
                    disabled={isBusy}
                    onPress={() => handleUndo(action)}
                    testID={`undo-action-${action.id}`}
                  />
                ) : null}
              </View>
            </View>
          );
        })}
      </View>
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
  title: {
    fontSize: typography.label,
    fontWeight: '700',
  },
  badge: {
    borderRadius: radii.sm,
    paddingHorizontal: spacing.xs,
    paddingVertical: 2,
  },
  badgeText: {
    fontSize: typography.caption,
    fontWeight: '700',
  },
  summaryText: {
    fontSize: typography.body,
    lineHeight: 20,
  },
  errorText: {
    fontSize: typography.caption,
    fontWeight: '500',
  },
  actionList: {
    gap: spacing.xs,
    marginTop: spacing.xs,
  },
  actionItem: {
    borderRadius: radii.md,
    gap: spacing.xs,
    padding: spacing.sm,
  },
  actionHeader: {
    flexDirection: 'row',
    justifyContent: 'space-between',
  },
  actionTitleContainer: {
    flex: 1,
    gap: 2,
  },
  actionTitle: {
    fontSize: typography.body,
    fontWeight: '600',
  },
  actionTime: {
    fontSize: typography.caption,
  },
  targetIdText: {
    fontSize: 11,
    fontFamily: 'monospace',
  },
  statusBadge: {
    alignSelf: 'flex-start',
    borderRadius: radii.sm,
    paddingHorizontal: spacing.xs,
    paddingVertical: 2,
  },
  statusBadgeText: {
    fontSize: typography.caption,
    fontWeight: '700',
  },
  actionButtonsRow: {
    flexDirection: 'row',
    justifyContent: 'flex-end',
    gap: spacing.xs,
  },
});
