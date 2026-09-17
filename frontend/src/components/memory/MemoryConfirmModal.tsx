import React from 'react';
import { StyleSheet, View } from 'react-native';
import { ActionButton, AppText, Card } from '../ui/Primitives';
import { useAppTheme } from '../../design/ThemeProvider';
import { radii, shadows, spacing, typography } from '../../theme';
import { strings } from '../../i18n/strings';

interface MemoryConfirmModalProps {
  confirmation: 'delete' | 'delete-all' | null;
  saving: boolean;
  onCancel: () => void;
  onConfirm: () => void;
}

export function MemoryConfirmModal({
  confirmation,
  saving,
  onCancel,
  onConfirm,
}: MemoryConfirmModalProps) {
  const { colors } = useAppTheme();

  if (!confirmation) return null;

  const isSingle = confirmation === 'delete';

  return (
    <Card
      style={[
        styles.card,
        {
          backgroundColor: colors.surface,
          borderColor: colors.error,
        },
      ]}
      testID="memory-confirmation"
    >
      <View style={styles.headerRow}>
        <View
          style={[
            styles.warningIcon,
            { backgroundColor: colors.errorContainer },
          ]}
        >
          <AppText style={styles.iconText}>⚠️</AppText>
        </View>
        <View style={styles.titleContainer}>
          <AppText style={[styles.title, { color: colors.error }]}>
            {isSingle ? strings.memory.deleteTitle : strings.memory.deleteAllTitle}
          </AppText>
          <AppText style={[styles.body, { color: colors.textMuted }]}>
            {isSingle ? strings.memory.deleteBody : strings.memory.deleteAllBody}
          </AppText>
        </View>
      </View>

      <View style={styles.actionsRow}>
        <ActionButton
          disabled={saving}
          label={strings.memory.cancel}
          onPress={onCancel}
          style={styles.actionHalf}
          testID="memory-confirm-cancel"
          variant="secondary"
        />
        <ActionButton
          disabled={saving}
          label={strings.memory.confirmDelete}
          onPress={onConfirm}
          style={styles.actionHalf}
          testID="memory-confirm-delete"
          variant="quiet"
        />
      </View>
    </Card>
  );
}

const styles = StyleSheet.create({
  card: {
    borderRadius: radii.md,
    borderWidth: 1.5,
    gap: spacing.md,
    marginTop: spacing.md,
    padding: spacing.md,
    ...shadows.md,
  },
  headerRow: {
    alignItems: 'flex-start',
    flexDirection: 'row',
    gap: spacing.sm,
  },
  warningIcon: {
    alignItems: 'center',
    borderRadius: radii.full,
    height: 36,
    justifyContent: 'center',
    width: 36,
  },
  iconText: {
    fontSize: 16,
  },
  titleContainer: {
    flex: 1,
    gap: spacing.xs,
  },
  title: {
    fontSize: typography.body,
    fontWeight: '700',
  },
  body: {
    fontSize: typography.caption,
    lineHeight: 18,
  },
  actionsRow: {
    flexDirection: 'row',
    gap: spacing.sm,
    marginTop: spacing.xs,
  },
  actionHalf: {
    flex: 1,
  },
});
