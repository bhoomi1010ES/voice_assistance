import React from 'react';
import { Pressable, StyleSheet, Text, View } from 'react-native';
import { AppText } from '../ui/Primitives';
import { useAppTheme } from '../../design/ThemeProvider';
import { radii, shadows, spacing } from '../../theme';
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
    <View
      style={[
        styles.card,
        {
          backgroundColor: colors.surface,
          borderColor: colors.borderSubtle,
        },
        shadows.sm,
      ]}
      testID="memory-settings-card"
    >
      <View style={styles.topRow}>
        <View style={styles.iconCircle}>
          <Text style={styles.icon}>🧠</Text>
        </View>

        <View style={styles.textContainer}>
          <View style={styles.titleRow}>
            <AppText style={[styles.title, { color: colors.text }]}>
              {strings.memory.settings}
            </AppText>
            <View
              style={[
                styles.badge,
                {
                  backgroundColor: enabled ? '#DCFCE7' : '#F3F4F6',
                },
              ]}
            >
              <Text
                style={[
                  styles.badgeText,
                  {
                    color: enabled ? '#15803D' : '#6B7280',
                  },
                ]}
              >
                {enabled ? 'Active' : 'Paused'}
              </Text>
            </View>
          </View>

          <Text style={[styles.description, { color: colors.textMuted }]}>
            {loading
              ? strings.memory.loading
              : enabled
              ? strings.memory.enabledDescription
              : strings.memory.disabledDescription}
          </Text>
        </View>
      </View>

      <Pressable
        accessibilityLabel={
          enabled ? strings.memory.turnOff : strings.memory.turnOn
        }
        accessibilityRole="switch"
        accessibilityState={{ checked: enabled }}
        disabled={loading || saving}
        onPress={onToggle}
        style={({ pressed }) => [
          styles.toggleButton,
          {
            backgroundColor: colors.surfaceLow,
            borderColor: colors.borderSubtle,
            opacity: loading || saving ? 0.6 : pressed ? 0.8 : 1,
          },
        ]}
        testID="memory-toggle"
      >
        <Text style={[styles.toggleButtonText, { color: colors.text }]}>
          {enabled ? strings.memory.turnOff : strings.memory.turnOn}
        </Text>
      </Pressable>
    </View>
  );
}

const styles = StyleSheet.create({
  card: {
    borderRadius: radii.xl,
    borderWidth: 1,
    gap: spacing.md,
    padding: spacing.md,
  },
  topRow: {
    alignItems: 'flex-start',
    flexDirection: 'row',
    gap: spacing.sm,
  },
  iconCircle: {
    alignItems: 'center',
    backgroundColor: '#FDE8E8',
    borderRadius: 22,
    height: 44,
    justifyContent: 'center',
    width: 44,
  },
  icon: {
    fontSize: 22,
  },
  textContainer: {
    flex: 1,
    gap: 4,
  },
  titleRow: {
    alignItems: 'center',
    flexDirection: 'row',
    gap: spacing.xs,
  },
  title: {
    fontSize: 17,
    fontWeight: '700',
  },
  badge: {
    borderRadius: 12,
    paddingHorizontal: 8,
    paddingVertical: 2,
  },
  badgeText: {
    fontSize: 12,
    fontWeight: '600',
  },
  description: {
    fontSize: 13,
    lineHeight: 18,
  },
  toggleButton: {
    alignItems: 'center',
    borderRadius: 14,
    borderWidth: 1,
    justifyContent: 'center',
    paddingVertical: 12,
    width: '100%',
  },
  toggleButtonText: {
    fontSize: 15,
    fontWeight: '600',
  },
});
