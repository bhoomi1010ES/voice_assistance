import React from 'react';
import { Pressable, StyleSheet, Text, View } from 'react-native';
import { useAppTheme } from '../../design/ThemeProvider';
import { radii, spacing, typography } from '../../theme';
import { strings } from '../../i18n/strings';
import { MemoryItem } from '../../memory/types';

interface MemoryCardProps {
  memory: MemoryItem;
  onView: (memory: MemoryItem) => void;
  selected?: boolean;
}

export function getCategoryMeta(type: string, content: string = '') {
  const lowerType = type?.toLowerCase() || '';
  const lowerContent = content.toLowerCase();

  if (lowerType === 'preference' || lowerContent.includes('prefer')) {
    return {
      icon: '❤️',
      label: 'Preference',
      iconBg: '#FFE4E6',
      badgeBg: '#FCE7F3',
      badgeColor: '#BE185D',
    };
  }
  if (
    lowerType === 'work' ||
    lowerContent.includes('work') ||
    lowerContent.includes('product management') ||
    lowerContent.includes('job')
  ) {
    return {
      icon: '🖥️',
      label: 'Work',
      iconBg: '#EDE9FE',
      badgeBg: '#EDE9FE',
      badgeColor: '#6D28D9',
    };
  }
  if (
    lowerType === 'travel' ||
    lowerContent.includes('travel') ||
    lowerContent.includes('trip') ||
    lowerContent.includes('japan')
  ) {
    return {
      icon: '✈️',
      label: 'Travel',
      iconBg: '#E0F2FE',
      badgeBg: '#E0F2FE',
      badgeColor: '#0369A1',
    };
  }
  if (
    lowerType === 'personal' ||
    lowerContent.includes('dog') ||
    lowerContent.includes('golden retriever') ||
    lowerContent.includes('family') ||
    lowerContent.includes('pet')
  ) {
    return {
      icon: '👤',
      label: 'Personal',
      iconBg: '#DCFCE7',
      badgeBg: '#DCFCE7',
      badgeColor: '#15803D',
    };
  }
  if (
    lowerType === 'shopping' ||
    lowerContent.includes('shop') ||
    lowerContent.includes('buy') ||
    lowerContent.includes('sustainable')
  ) {
    return {
      icon: '🛒',
      label: 'Shopping',
      iconBg: '#FFEDD5',
      badgeBg: '#FFEDD5',
      badgeColor: '#C2410C',
    };
  }

  return {
    icon: '✦',
    label: type ? type.charAt(0).toUpperCase() + type.slice(1) : 'Fact',
    iconBg: '#EDE9FE',
    badgeBg: '#F1F5F9',
    badgeColor: '#64748B',
  };
}

export function MemoryCard({
  memory,
  onView,
  selected = false,
}: MemoryCardProps) {
  const { colors } = useAppTheme();
  const meta = getCategoryMeta(memory.memory_type, memory.content);

  return (
    <Pressable
      accessibilityLabel={`${strings.memory.itemLabel}: ${memory.content}`}
      accessibilityRole="button"
      onPress={() => onView(memory)}
      style={({ pressed }) => [
        styles.row,
        {
          backgroundColor: selected ? colors.surfaceLow : colors.surface,
          borderColor: selected ? colors.primary : colors.borderSubtle,
          opacity: pressed ? 0.75 : 1,
        },
      ]}
      testID="memory-view"
    >
      <View style={[styles.iconCircle, { backgroundColor: meta.iconBg }]}>
        <Text style={styles.icon}>{meta.icon}</Text>
      </View>

      <View style={[styles.badge, { backgroundColor: meta.badgeBg }]}>
        <Text style={[styles.badgeText, { color: meta.badgeColor }]}>
          {meta.label}
        </Text>
      </View>

      <Text
        ellipsizeMode="tail"
        numberOfLines={1}
        style={[styles.content, { color: colors.text }]}
        testID="memory-item-content"
      >
        {memory.content}
      </Text>

      <Text style={[styles.chevron, { color: colors.textMuted }]}>›</Text>
    </Pressable>
  );
}

export function memoryTypeLabel(type: MemoryItem['memory_type']): string {
  return type.charAt(0).toUpperCase() + type.slice(1);
}

const styles = StyleSheet.create({
  row: {
    alignItems: 'center',
    borderRadius: radii.lg,
    borderWidth: 1,
    flexDirection: 'row',
    gap: spacing.xs + 2,
    marginTop: spacing.xs,
    paddingHorizontal: spacing.sm + 2,
    paddingVertical: spacing.sm + 2,
  },
  iconCircle: {
    alignItems: 'center',
    borderRadius: 16,
    height: 32,
    justifyContent: 'center',
    width: 32,
  },
  icon: {
    fontSize: 14,
  },
  badge: {
    borderRadius: radii.pill,
    paddingHorizontal: spacing.sm,
    paddingVertical: 3,
  },
  badgeText: {
    fontSize: 11,
    fontWeight: '700',
  },
  content: {
    flex: 1,
    fontSize: typography.caption + 1,
  },
  chevron: {
    fontSize: 18,
    fontWeight: '600',
    paddingHorizontal: 2,
  },
});
