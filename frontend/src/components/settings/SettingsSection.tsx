import React from 'react';
import { StyleSheet, View } from 'react-native';
import { AppText, Card } from '../ui/Primitives';
import { useAppTheme } from '../../design/ThemeProvider';
import { radii, shadows, spacing, typography } from '../../theme';

interface SettingsSectionProps {
  title?: string;
  children: React.ReactNode;
}

export function SettingsSection({ title, children }: SettingsSectionProps) {
  const { colors } = useAppTheme();

  return (
    <View style={styles.container}>
      {title ? (
        <AppText style={[styles.title, { color: colors.primary }]}>
          {title}
        </AppText>
      ) : null}
      <Card
        style={[
          styles.card,
          {
            backgroundColor: colors.surface,
            borderColor: colors.border,
          },
        ]}
      >
        {children}
      </Card>
    </View>
  );
}

const styles = StyleSheet.create({
  container: {
    gap: spacing.xs,
    marginTop: spacing.sm,
  },
  title: {
    fontSize: typography.caption,
    fontWeight: '700',
    letterSpacing: 0.8,
    paddingHorizontal: spacing.xs,
    textTransform: 'uppercase',
  },
  card: {
    borderRadius: radii.md,
    borderWidth: 1,
    overflow: 'hidden',
    padding: 0,
    ...shadows.sm,
  },
});
