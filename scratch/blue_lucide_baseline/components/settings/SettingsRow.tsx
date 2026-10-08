import React from 'react';
import { Pressable, StyleSheet, View } from 'react-native';
import { AppText } from '../ui/Primitives';
import { useAppTheme } from '../../design/ThemeProvider';
import { radii, spacing, typography } from '../../theme';

interface SettingsRowProps {
  icon: React.ReactNode;
  title: string;
  subtitle?: string;
  badge?: string;
  onPress?: () => void;
  testID?: string;
  destructive?: boolean;
  disabled?: boolean;
  showDivider?: boolean;
  iconTone?: 'primary' | 'blue';
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
  iconTone = 'primary',
}: SettingsRowProps) {
  const { colors, mode } = useAppTheme();

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
                : iconTone === 'blue' && mode === 'light'
                ? '#E8F2FF'
                : colors.primaryContainer,
            },
          ]}
        >
          {typeof icon === 'string' ? (
            <AppText style={styles.icon}>{icon}</AppText>
          ) : (
            icon
          )}
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
              style={[styles.badgeText, { color: colors.onSecondaryContainer }]}
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
    paddingVertical: 16,
  },
  iconContainer: {
    alignItems: 'center',
    borderRadius: radii.full,
    height: 46,
    justifyContent: 'center',
    width: 46,
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
    fontWeight: '700',
  },
  subtitle: {
    fontSize: 13,
    lineHeight: 20,
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
    marginLeft: spacing.md,
    marginRight: spacing.md,
  },
});
