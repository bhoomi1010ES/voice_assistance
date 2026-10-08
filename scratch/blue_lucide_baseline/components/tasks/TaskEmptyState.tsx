import React from 'react';
import { StyleSheet, View } from 'react-native';
import { AppText, Card } from '../ui/Primitives';
import { useAppTheme } from '../../design/ThemeProvider';
import { radii, spacing, typography } from '../../theme';

interface TaskEmptyStateProps {
  text: string;
  testID: string;
  icon?: string;
  title?: string;
  hint?: string;
}

export function TaskEmptyState({
  text,
  testID,
  icon = '✨',
  title,
  hint,
}: TaskEmptyStateProps) {
  const { colors } = useAppTheme();

  return (
    <Card
      style={[
        styles.card,
        {
          backgroundColor: colors.surfaceLow,
          borderColor: colors.borderSubtle,
        },
      ]}
      testID={testID}
    >
      <View
        style={[
          styles.iconContainer,
          {
            backgroundColor: colors.surface,
            borderColor: colors.border,
          },
        ]}
      >
        <AppText style={styles.icon}>{icon}</AppText>
      </View>

      {title ? (
        <AppText style={[styles.title, { color: colors.text }]}>
          {title}
        </AppText>
      ) : null}

      <AppText style={[styles.text, { color: colors.textMuted }]}>
        {text}
      </AppText>

      {hint ? (
        <View
          style={[
            styles.hintBadge,
            {
              backgroundColor: colors.surface,
              borderColor: colors.borderSubtle,
            },
          ]}
        >
          <AppText style={[styles.hintText, { color: colors.textSubtle }]}>
            {hint}
          </AppText>
        </View>
      ) : null}
    </Card>
  );
}

const styles = StyleSheet.create({
  card: {
    alignItems: 'center',
    borderRadius: radii.lg,
    borderWidth: 1,
    gap: spacing.sm,
    justifyContent: 'center',
    marginTop: spacing.md,
    paddingHorizontal: spacing.lg,
    paddingVertical: spacing.xl,
    textAlign: 'center',
  },
  iconContainer: {
    alignItems: 'center',
    borderRadius: radii.full,
    borderWidth: 1,
    height: 56,
    justifyContent: 'center',
    marginBottom: spacing.xs,
    width: 56,
  },
  icon: {
    fontSize: 24,
  },
  title: {
    fontSize: typography.heading,
    fontWeight: '700',
    textAlign: 'center',
  },
  text: {
    fontSize: typography.body,
    lineHeight: 22,
    textAlign: 'center',
  },
  hintBadge: {
    borderRadius: radii.pill,
    borderWidth: 1,
    marginTop: spacing.xs,
    paddingHorizontal: spacing.md,
    paddingVertical: spacing.xs,
  },
  hintText: {
    fontSize: typography.caption,
    textAlign: 'center',
  },
});
