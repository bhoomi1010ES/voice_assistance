import React from 'react';
import { StyleSheet, View } from 'react-native';
import { ActionButton, AppText, Card } from '../ui/Primitives';
import { useAppTheme } from '../../design/ThemeProvider';
import { radii, spacing, typography } from '../../theme';
import { strings } from '../../i18n/strings';

export type PushPermissionState =
  | 'unknown'
  | 'denied'
  | 'granted_no_token'
  | 'ready'
  | 'unavailable';

export interface PushPermissionCardProps {
  state: PushPermissionState | 'granted';
  onRequest: () => void;
  onOpenSettings: () => void;
  loading?: boolean;
}

export function PushPermissionCard({
  state,
  onRequest,
  onOpenSettings,
  loading = false,
}: PushPermissionCardProps) {
  const { colors } = useAppTheme();
  const normalizedState: PushPermissionState =
    state === 'granted' ? 'ready' : state;

  const description = (() => {
    switch (normalizedState) {
      case 'ready':
        return strings.tasks.pushReady;
      case 'denied':
        return strings.tasks.pushDenied;
      case 'granted_no_token':
        return strings.tasks.pushGrantedNoToken;
      case 'unavailable':
        return strings.tasks.pushUnavailable;
      case 'unknown':
      default:
        return strings.tasks.pushUnknown;
    }
  })();

  const icon = (() => {
    switch (normalizedState) {
      case 'ready':
        return '🔔';
      case 'denied':
        return '🔕';
      case 'granted_no_token':
        return '⚠️';
      case 'unavailable':
        return 'ℹ️';
      case 'unknown':
      default:
        return '📱';
    }
  })();

  return (
    <Card
      style={[
        styles.card,
        {
          backgroundColor:
            normalizedState === 'ready' ? colors.surfaceLow : colors.surface,
          borderColor:
            normalizedState === 'denied' ||
            normalizedState === 'granted_no_token'
              ? colors.warning
              : normalizedState === 'ready'
              ? colors.borderSubtle
              : colors.border,
        },
      ]}
      testID="push-permission-card"
    >
      <View style={styles.headerRow}>
        <View
          style={[
            styles.iconCircle,
            { backgroundColor: colors.primaryContainer },
          ]}
        >
          <AppText style={styles.icon}>{icon}</AppText>
        </View>
        <View style={styles.content}>
          <AppText style={[styles.title, { color: colors.text }]}>
            {strings.tasks.pushTitle}
          </AppText>
          <AppText
            style={[styles.description, { color: colors.textMuted }]}
            testID="push-permission-description"
          >
            {description}
          </AppText>
        </View>
      </View>

      {normalizedState === 'denied' ? (
        <View style={styles.actionContainer}>
          <ActionButton
            label={strings.tasks.openSettings}
            onPress={onOpenSettings}
            testID="push-open-settings"
            variant="secondary"
          />
        </View>
      ) : normalizedState === 'unknown' ||
        normalizedState === 'granted_no_token' ? (
        <View style={styles.actionContainer}>
          <ActionButton
            disabled={loading}
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
    borderRadius: 22,
    borderWidth: 1,
    gap: spacing.sm,
    marginTop: spacing.sm,
    padding: spacing.md,
  },
  headerRow: {
    flexDirection: 'row',
    gap: 12,
  },
  iconCircle: {
    alignItems: 'center',
    justifyContent: 'center',
    width: 42,
    height: 42,
    borderRadius: radii.full,
  },
  icon: {
    fontSize: 22,
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
    fontSize: 13,
    lineHeight: 20,
  },
  actionContainer: {
    alignItems: 'flex-start',
    marginTop: spacing.xs,
  },
});
