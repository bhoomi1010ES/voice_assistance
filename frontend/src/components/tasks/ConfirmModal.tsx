import React from 'react';
import { Modal, StyleSheet, View } from 'react-native';
import { ActionButton, AppText, Card, Heading } from '../ui/Primitives';
import { useAppTheme } from '../../design/ThemeProvider';
import { radii, shadows, spacing, typography } from '../../theme';
import { strings } from '../../i18n/strings';

export type Confirmation =
  | { kind: 'todo-delete'; id: string; label: string }
  | { kind: 'todo-complete'; id: string; label: string }
  | { kind: 'reminder-delete'; id: string; label: string };

interface ConfirmModalProps {
  confirmation: Confirmation | null;
  busy: boolean;
  onCancel: () => void;
  onConfirm: () => void;
}

export function ConfirmModal({
  confirmation,
  busy,
  onCancel,
  onConfirm,
}: ConfirmModalProps) {
  const { colors } = useAppTheme();

  return (
    <Modal
      animationType="fade"
      onRequestClose={onCancel}
      transparent
      visible={Boolean(confirmation)}
    >
      <View style={styles.overlay}>
        <Card
          style={[
            styles.card,
            {
              backgroundColor: colors.surface,
              borderColor: colors.border,
            },
          ]}
        >
          <Heading>{strings.tasks.confirmTitle}</Heading>
          <AppText style={[styles.body, { color: colors.textMuted }]}>
            {confirmation
              ? `${strings.tasks.confirmBody} “${confirmation.label}”`
              : ''}
          </AppText>
          <View style={styles.actions}>
            <ActionButton
              label={strings.tasks.cancel}
              onPress={onCancel}
              testID="action-cancel"
              variant="quiet"
            />
            <ActionButton
              disabled={busy}
              label={strings.tasks.confirm}
              onPress={onConfirm}
              testID="action-confirm"
            />
          </View>
        </Card>
      </View>
    </Modal>
  );
}

const styles = StyleSheet.create({
  overlay: {
    alignItems: 'center',
    backgroundColor: 'rgba(0, 0, 0, 0.5)',
    flex: 1,
    justifyContent: 'center',
    padding: spacing.lg,
  },
  card: {
    borderRadius: radii.lg,
    borderWidth: 1,
    gap: spacing.md,
    maxWidth: 400,
    padding: spacing.lg,
    width: '100%',
    ...shadows.lg,
  },
  body: {
    fontSize: typography.body,
    lineHeight: 22,
  },
  actions: {
    flexDirection: 'row',
    flexWrap: 'wrap',
    gap: spacing.sm,
    justifyContent: 'flex-end',
    marginTop: spacing.sm,
  },
});
