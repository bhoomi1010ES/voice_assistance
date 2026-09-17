import React from 'react';
import { StyleSheet, View } from 'react-native';
import { ActionButton, AppText, Card } from '../ui/Primitives';
import { useAppTheme } from '../../design/ThemeProvider';
import { radii, shadows, spacing, typography } from '../../theme';
import { strings } from '../../i18n/strings';

interface MemorySettingsCardProps {
  enabled: boolean;
  saving: boolean;
  loading: boolean;
  onToggle: () => void;
}

export function MemorySettingsCard({
  enabled,
  saving,
  loading,
  onToggle,
}: MemorySettingsCardProps) {
  const { colors } = useAppTheme();

  return (
    <Card
      style={[
        styles.card,
        {
          backgroundColor: colors.surface,
          borderColor: colors.border,
        },
      ]}
      testID="memory-settings-card"
    >
      <View style={styles.headerRow}>
        <View
          style={[
            styles.iconContainer,
            {
              backgroundColor: colors.primaryContainer,
            },
          ]}
        >
          <AppText style={styles.icon}>🧠</AppText>
        </View>

        <View style={styles.titleContainer}>
          <View style={styles.titleRow}>
            <AppText style={[styles.title, { color: colors.text }]}>
              {strings.memory.settings}
            </AppText>
            <View
              style={[
                styles.statusBadge,
                {
                  backgroundColor: enabled
                    ? colors.successContainer
                    : colors.surfaceMuted,
                },
              ]}
            >
              <AppText
                style={[
                  styles.statusText,
                  {
                    color: enabled ? colors.success : colors.textMuted,
                  },
                ]}
              >
                {enabled ? 'Active' : 'Paused'}
              </AppText>
            </View>
          </View>
          <AppText style={[styles.description, { color: colors.textMuted }]}>
            {loading
              ? strings.memory.loading
              : enabled
              ? strings.memory.enabledDescription
              : strings.memory.disabledDescription}
          </AppText>
        </View>
      </View>

      <ActionButton
        accessibilityLabel={
          enabled ? strings.memory.turnOff : strings.memory.turnOn
        }
        accessibilityRole="switch"
        accessibilityState={{ checked: enabled }}
        disabled={loading || saving}
        label={enabled ? strings.memory.turnOff : strings.memory.turnOn}
        onPress={onToggle}
        style={styles.actionButton}
        testID="memory-toggle"
        variant={enabled ? 'secondary' : 'primary'}
      />
    </Card>
  );
}

const styles = StyleSheet.create({
  card: {
    borderRadius: radii.md,
    borderWidth: 1,
    gap: spacing.md,
    padding: spacing.md,
    ...shadows.sm,
  },
  headerRow: {
    alignItems: 'flex-start',
    flexDirection: 'row',
    gap: spacing.sm,
  },
  iconContainer: {
    alignItems: 'center',
    borderRadius: radii.full,
    height: 40,
    justifyContent: 'center',
    marginTop: 2,
    width: 40,
  },
  icon: {
    fontSize: 18,
  },
  titleContainer: {
    flex: 1,
    gap: spacing.xs,
  },
  titleRow: {
    alignItems: 'center',
    flexDirection: 'row',
    gap: spacing.xs,
  },
  title: {
    fontSize: typography.subheading,
    fontWeight: '700',
    letterSpacing: -0.2,
  },
  statusBadge: {
    borderRadius: radii.pill,
    paddingHorizontal: spacing.sm,
    paddingVertical: 2,
  },
  statusText: {
    fontSize: typography.caption,
    fontWeight: '700',
  },
  description: {
    fontSize: typography.caption,
    lineHeight: 18,
  },
  actionButton: {
    marginTop: spacing.xs,
  },
});
