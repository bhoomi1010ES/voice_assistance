import React from 'react';
import { StyleSheet, View } from 'react-native';
import { ActionButton, AppText, Card } from '../ui/Primitives';
import { useAppTheme } from '../../design/ThemeProvider';
import { radii, shadows, spacing, typography } from '../../theme';
import { strings } from '../../i18n/strings';

interface SessionDeviceCardProps {
  title: string;
  subtitle: string;
  isRevoked: boolean;
  revoking: boolean;
  onRevoke: () => void;
  icon?: string;
  testID?: string;
}

export function SessionDeviceCard({
  title,
  subtitle,
  isRevoked,
  revoking,
  onRevoke,
  icon = '📱',
  testID,
}: SessionDeviceCardProps) {
  const { colors } = useAppTheme();

  return (
    <Card
      style={[
        styles.card,
        {
          backgroundColor: isRevoked ? colors.surfaceLow : colors.surface,
          borderColor: isRevoked ? colors.borderSubtle : colors.border,
          opacity: isRevoked ? 0.7 : 1,
        },
      ]}
      testID={testID}
    >
      <View style={styles.headerRow}>
        <View
          style={[
            styles.iconContainer,
            {
              backgroundColor: isRevoked
                ? colors.surfaceMuted
                : colors.surfaceLow,
            },
          ]}
        >
          <AppText style={styles.icon}>{icon}</AppText>
        </View>

        <View style={styles.info}>
          <AppText style={[styles.title, { color: colors.text }]}>
            {title}
          </AppText>
          <AppText style={[styles.subtitle, { color: colors.textMuted }]}>
            {subtitle}
          </AppText>
        </View>

        {isRevoked ? (
          <View
            style={[
              styles.revokedBadge,
              { backgroundColor: colors.surfaceMuted },
            ]}
          >
            <AppText
              style={[styles.revokedText, { color: colors.textSubtle }]}
            >
              {strings.sessions.revoked}
            </AppText>
          </View>
        ) : null}
      </View>

      {!isRevoked ? (
        <View style={styles.actionRow}>
          <ActionButton
            disabled={revoking}
            label={
              revoking
                ? strings.sessions.revoked
                : strings.sessions.revoke
            }
            onPress={onRevoke}
            style={styles.revokeButton}
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
    ...shadows.sm,
  },
  headerRow: {
    alignItems: 'center',
    flexDirection: 'row',
    gap: spacing.sm,
  },
  iconContainer: {
    alignItems: 'center',
    borderRadius: radii.sm,
    height: 40,
    justifyContent: 'center',
    width: 40,
  },
  icon: {
    fontSize: 20,
  },
  info: {
    flex: 1,
    gap: 2,
  },
  title: {
    fontSize: typography.body,
    fontWeight: '600',
  },
  subtitle: {
    fontSize: typography.caption,
    lineHeight: 16,
  },
  revokedBadge: {
    borderRadius: radii.pill,
    paddingHorizontal: spacing.sm,
    paddingVertical: 3,
  },
  revokedText: {
    fontSize: typography.caption,
    fontWeight: '600',
  },
  actionRow: {
    alignItems: 'flex-end',
    marginTop: spacing.xs,
  },
  revokeButton: {
    minWidth: 90,
  },
});
