import React from 'react';
import {
  Pressable,
  ScrollView,
  StyleSheet,
  Text,
  View,
} from 'react-native';
import { useAppTheme } from '../../design/ThemeProvider';
import { radii, shadows, spacing, typography } from '../../design/tokens';

export type QuickChip = {
  id: string;
  label: string;
  icon: string;
};

export type QuickActionChipsProps = {
  chips?: QuickChip[];
  onChipPress?: (chip: QuickChip) => void;
};

const DEFAULT_CHIPS: QuickChip[] = [
  { id: 'plan', label: 'Plan my day', icon: '✓' },
  { id: 'remind', label: 'Set a reminder', icon: '🔔' },
  { id: 'tasks', label: 'Check my tasks', icon: '📋' },
  { id: 'notes', label: 'What did we talk about?', icon: '✦' },
];

export function QuickActionChips({
  chips = DEFAULT_CHIPS,
  onChipPress,
}: QuickActionChipsProps) {
  const { colors } = useAppTheme();

  return (
    <View style={styles.container}>
      <Text style={[styles.sectionTitle, { color: colors.textSubtle }]}>
        Example prompts (Say aloud)
      </Text>
      <ScrollView
        horizontal
        showsHorizontalScrollIndicator={false}
        contentContainerStyle={styles.scrollContent}
      >
        {chips.map(chip => (
          <Pressable
            key={chip.id}
            accessibilityLabel={`Example prompt: ${chip.label}`}
            accessibilityRole="button"
            hitSlop={spacing.xxs}
            onPress={() => onChipPress?.(chip)}
            style={({ pressed }) => [
              styles.chip,
              {
                backgroundColor: colors.surface,
                borderColor: colors.borderSubtle,
                opacity: pressed ? 0.75 : 1,
              },
              shadows.sm,
            ]}
          >
            <Text style={[styles.chipIcon, { color: colors.primary }]}>
              {chip.icon}
            </Text>
            <Text style={[styles.chipLabel, { color: colors.text }]}>
              {chip.label}
            </Text>
          </Pressable>
        ))}
      </ScrollView>
    </View>
  );
}

const styles = StyleSheet.create({
  container: {
    marginVertical: spacing.sm,
    width: '100%',
  },
  sectionTitle: {
    fontSize: typography.caption,
    fontWeight: '600',
    letterSpacing: 0.6,
    marginBottom: spacing.xs,
    paddingHorizontal: spacing.sm,
    textTransform: 'uppercase',
  },
  scrollContent: {
    gap: spacing.xs,
    paddingHorizontal: spacing.sm,
    paddingVertical: spacing.xxs,
  },
  chip: {
    alignItems: 'center',
    borderRadius: radii.full,
    borderWidth: 1,
    flexDirection: 'row',
    minHeight: 48,
    paddingHorizontal: spacing.md,
    paddingVertical: spacing.xs,
  },
  chipIcon: {
    fontSize: 14,
    marginRight: spacing.xs,
  },
  chipLabel: {
    fontSize: typography.bodySm,
    fontWeight: '500',
  },
});
