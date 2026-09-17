import React from 'react';
import { Pressable, StyleSheet, View } from 'react-native';
import { AppText } from '../ui/Primitives';
import { useAppTheme } from '../../design/ThemeProvider';
import { radii, spacing, typography } from '../../theme';

interface SettingsRowProps {
  icon: string;
  title: string;
  subtitle?: string;
  badge?: string;
  onPress?: () => void;
  testID?: string;
  destructive?: boolean;
  disabled?: boolean;
  showDivider?: boolean;
}

export function SettingsRow({
  icon,
  title,
  subtitle,
  badge,
  onPress,
  testID,
  destructive = false,
  disabled = false,
  showDivider = false,
}: SettingsRowProps) {
  const { colors } = useAppTheme();

  return (
    <>
      <Pressable
        accessibilityLabel={title}
        accessibilityRole="button"
        disabled={disabled || !onPress}
        onPress={onPress}
        style={({ pressed }) => [
          styles.row,
          {
            backgroundColor: pressed ? colors.surfaceLow : colors.surface,
            opacity: disabled ? 0.5 : 1,
          },
        ]}
        testID={testID}
      >
        <View
          style={[
            styles.iconContainer,
            {
              backgroundColor: destructive
                ? colors.errorContainer
                : colors.surfaceMuted,
            },
          ]}
        >
          <AppText style={styles.icon}>{icon}</AppText>
        </View>

        <View style={styles.textContainer}>
          <AppText
            style={[
              styles.title,
              { color: destructive ? colors.error : colors.text },
            ]}
          >
            {title}
          </AppText>
          {subtitle ? (
            <AppText style={[styles.subtitle, { color: colors.textMuted }]}>
              {subtitle}
            </AppText>
          ) : null}
        </View>

        {badge ? (
          <View
            style={[
              styles.badge,
              { backgroundColor: colors.secondaryContainer },
            ]}
          >
            <AppText
              style={[
                styles.badgeText,
                { color: colors.onSecondaryContainer },
              ]}
            >
              {badge}
            </AppText>
          </View>
        ) : null}

        {onPress ? (
          <AppText
            style={[
              styles.chevron,
              { color: destructive ? colors.error : colors.textSubtle },
            ]}
          >
            ›
          </AppText>
        ) : null}
      </Pressable>
      {showDivider ? (
        <View
          style={[styles.divider, { backgroundColor: colors.borderSubtle }]}
        />
      ) : null}
    </>
  );
}

const styles = StyleSheet.create({
  row: {
    alignItems: 'center',
    flexDirection: 'row',
    gap: spacing.md,
    minHeight: 56,
    paddingHorizontal: spacing.md,
    paddingVertical: spacing.sm + 2,
  },
  iconContainer: {
    alignItems: 'center',
    borderRadius: radii.sm,
    height: 38,
    justifyContent: 'center',
    width: 38,
  },
  icon: {
    fontSize: 18,
  },
  textContainer: {
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
  badge: {
    borderRadius: radii.pill,
    paddingHorizontal: spacing.sm,
    paddingVertical: 2,
  },
  badgeText: {
    fontSize: typography.caption,
    fontWeight: '700',
  },
  chevron: {
    fontSize: 22,
    fontWeight: '400',
    lineHeight: 22,
  },
  divider: {
    height: 1,
    marginLeft: spacing.md + 38 + spacing.md,
  },
});
