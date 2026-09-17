import React from 'react';
import { StyleSheet, View } from 'react-native';
import { ActionButton, AppText, Card } from '../ui/Primitives';
import { useAppTheme } from '../../design/ThemeProvider';
import { radii, spacing, typography } from '../../theme';
import { strings } from '../../i18n/strings';

interface PushPermissionCardProps {
  state: 'unknown' | 'granted' | 'denied';
  onRequest: () => void;
  onOpenSettings: () => void;
}

export function PushPermissionCard({
  state,
  onRequest,
  onOpenSettings,
}: PushPermissionCardProps) {
  const { colors } = useAppTheme();

  return (
    <Card
      style={[
        styles.card,
        {
          backgroundColor:
            state === 'granted' ? colors.surfaceLow : colors.surface,
          borderColor:
            state === 'denied'
              ? colors.warning
              : state === 'granted'
              ? colors.borderSubtle
              : colors.border,
        },
      ]}
      testID="push-permission-card"
    >
      <View style={styles.headerRow}>
        <AppText style={styles.icon}>
          {state === 'granted' ? '🔔' : state === 'denied' ? '🔕' : '📱'}
        </AppText>
        <View style={styles.content}>
          <AppText style={[styles.title, { color: colors.text }]}>
            {strings.tasks.pushTitle}
          </AppText>
          <AppText style={[styles.description, { color: colors.textMuted }]}>
            {state === 'granted'
              ? strings.tasks.pushEnabled
              : state === 'denied'
              ? strings.tasks.pushDenied
              : strings.tasks.pushDescription}
          </AppText>
        </View>
      </View>

      {state === 'denied' ? (
        <View style={styles.actionContainer}>
          <ActionButton
            label={strings.tasks.openSettings}
            onPress={onOpenSettings}
            testID="push-open-settings"
            variant="secondary"
          />
        </View>
      ) : state !== 'granted' ? (
        <View style={styles.actionContainer}>
          <ActionButton
            label={strings.tasks.enablePush}
            onPress={onRequest}
            testID="push-enable"
            variant="secondary"
          />
        </View>
      ) : null}
    </Card>
  );
}

const styles = StyleSheet.create({
  card: {
    borderRadius: radii.md,
    borderWidth: 1,
    gap: spacing.sm,
    marginTop: spacing.sm,
    padding: spacing.md,
  },
  headerRow: {
    flexDirection: 'row',
    gap: spacing.sm,
  },
  icon: {
    fontSize: 20,
    marginTop: 2,
  },
  content: {
    flex: 1,
    gap: spacing.xs,
  },
  title: {
    fontSize: typography.body,
    fontWeight: '700',
  },
  description: {
    fontSize: typography.caption,
    lineHeight: 18,
  },
  actionContainer: {
    alignItems: 'flex-start',
    marginTop: spacing.xs,
  },
});
