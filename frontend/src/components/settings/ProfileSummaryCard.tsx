import React from 'react';
import { Pressable, StyleSheet, View } from 'react-native';
import { AppText, Card } from '../ui/Primitives';
import { useAppTheme } from '../../design/ThemeProvider';
import { radii, shadows, spacing, typography } from '../../theme';

interface ProfileSummaryCardProps {
  name?: string;
  email?: string;
  status?: string;
  onPress?: () => void;
  testID?: string;
}

export function ProfileSummaryCard({
  name = 'User',
  email = '',
  status = 'active',
  onPress,
  testID,
}: ProfileSummaryCardProps) {
  const { colors } = useAppTheme();

  const initials = name
    .split(' ')
    .map(part => part.charAt(0))
    .slice(0, 2)
    .join('')
    .toUpperCase() || 'U';

  return (
    <Card
      style={[
        styles.card,
        {
          backgroundColor: colors.surface,
          borderColor: colors.border,
        },
      ]}
    >
      <Pressable
        accessibilityLabel={`User profile: ${name}`}
        accessibilityRole="button"
        disabled={!onPress}
        onPress={onPress}
        style={styles.innerPressable}
        testID={testID}
      >
        <View
          style={[
            styles.avatar,
            {
              backgroundColor: colors.primaryContainer,
              borderColor: colors.border,
            },
          ]}
        >
          <AppText
            style={[
              styles.avatarText,
              { color: colors.onPrimaryContainer },
            ]}
          >
            {initials}
          </AppText>
        </View>

        <View style={styles.info}>
          <View style={styles.nameRow}>
            <AppText style={[styles.name, { color: colors.text }]}>
              {name}
            </AppText>
            {status ? (
              <View
                style={[
                  styles.statusBadge,
                  { backgroundColor: colors.successContainer },
                ]}
              >
                <AppText
                  style={[styles.statusText, { color: colors.success }]}
                >
                  {status}
                </AppText>
              </View>
            ) : null}
          </View>
          {email ? (
            <AppText style={[styles.email, { color: colors.textMuted }]}>
              {email}
            </AppText>
          ) : null}
        </View>

        {onPress ? (
          <AppText style={[styles.chevron, { color: colors.textSubtle }]}>
            ›
          </AppText>
        ) : null}
      </Pressable>
    </Card>
  );
}

const styles = StyleSheet.create({
  card: {
    borderRadius: radii.md,
    borderWidth: 1,
    overflow: 'hidden',
    padding: 0,
    ...shadows.sm,
  },
  innerPressable: {
    alignItems: 'center',
    flexDirection: 'row',
    gap: spacing.md,
    minHeight: 72,
    paddingHorizontal: spacing.md,
    paddingVertical: spacing.md,
  },
  avatar: {
    alignItems: 'center',
    borderRadius: radii.full,
    borderWidth: 1.5,
    height: 48,
    justifyContent: 'center',
    width: 48,
  },
  avatarText: {
    fontSize: typography.body,
    fontWeight: '700',
    letterSpacing: 0.5,
  },
  info: {
    flex: 1,
    gap: 2,
  },
  nameRow: {
    alignItems: 'center',
    flexDirection: 'row',
    gap: spacing.xs,
  },
  name: {
    fontSize: typography.subheading,
    fontWeight: '700',
    letterSpacing: -0.2,
  },
  statusBadge: {
    borderRadius: radii.pill,
    paddingHorizontal: spacing.sm,
    paddingVertical: 1,
  },
  statusText: {
    fontSize: typography.caption,
    fontWeight: '600',
    textTransform: 'capitalize',
  },
  email: {
    fontSize: typography.caption,
  },
  chevron: {
    fontSize: 24,
    lineHeight: 24,
  },
});
