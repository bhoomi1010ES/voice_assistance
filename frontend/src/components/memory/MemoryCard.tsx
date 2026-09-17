import React from 'react';
import { StyleSheet, View } from 'react-native';
import { ActionButton, AppText, Card } from '../ui/Primitives';
import { useAppTheme } from '../../design/ThemeProvider';
import { radii, shadows, spacing, typography } from '../../theme';
import { strings } from '../../i18n/strings';
import { MemoryItem } from '../../memory/types';

interface MemoryCardProps {
  memory: MemoryItem;
  onView: (memory: MemoryItem) => void;
  selected?: boolean;
}

export function MemoryCard({ memory, onView, selected = false }: MemoryCardProps) {
  const { colors } = useAppTheme();

  return (
    <Card
      style={[
        styles.card,
        {
          backgroundColor: selected ? colors.surfaceLow : colors.surface,
          borderColor: selected ? colors.primary : colors.border,
        },
      ]}
      testID="memory-item"
    >
      <View style={styles.headerRow}>
        <View
          style={[
            styles.typeBadge,
            {
              backgroundColor: colors.surfaceMuted,
            },
          ]}
        >
          <View
            style={[
              styles.typeDot,
              {
                backgroundColor:
                  memory.memory_type === 'preference'
                    ? colors.secondary
                    : memory.memory_type === 'fact'
                    ? colors.primary
                    : colors.tertiary,
              },
            ]}
          />
          <AppText
            style={[
              styles.typeText,
              { color: colors.textMuted },
            ]}
          >
            {memoryTypeLabel(memory.memory_type)}
          </AppText>
        </View>

        {memory.supersedes_id ? (
          <View
            style={[
              styles.replacementBadge,
              {
                backgroundColor: colors.warningContainer,
              },
            ]}
          >
            <AppText
              style={[
                styles.replacementText,
                { color: colors.warning },
              ]}
            >
              {strings.memory.replacementNotice}
            </AppText>
          </View>
        ) : null}
      </View>

      <AppText
        accessibilityLabel={`${strings.memory.itemLabel}: ${memory.content}`}
        style={[styles.content, { color: colors.text }]}
        testID="memory-item-content"
      >
        {memory.content}
      </AppText>

      <View style={styles.actionsRow}>
        <ActionButton
          label={strings.memory.view}
          onPress={() => onView(memory)}
          style={styles.viewButton}
          testID="memory-view"
          variant="secondary"
        />
      </View>
    </Card>
  );
}

export function memoryTypeLabel(type: MemoryItem['memory_type']): string {
  return type.charAt(0).toUpperCase() + type.slice(1);
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
    flexWrap: 'wrap',
    gap: spacing.xs,
  },
  typeBadge: {
    alignItems: 'center',
    borderRadius: radii.pill,
    flexDirection: 'row',
    gap: 6,
    paddingHorizontal: spacing.sm,
    paddingVertical: 3,
  },
  typeDot: {
    borderRadius: radii.full,
    height: 6,
    width: 6,
  },
  typeText: {
    fontSize: typography.caption,
    fontWeight: '700',
  },
  replacementBadge: {
    borderRadius: radii.pill,
    paddingHorizontal: spacing.sm,
    paddingVertical: 3,
  },
  replacementText: {
    fontSize: typography.caption,
    fontWeight: '600',
  },
  content: {
    fontSize: typography.body,
    lineHeight: 22,
    marginTop: 2,
  },
  actionsRow: {
    flexDirection: 'row',
    justifyContent: 'flex-end',
    marginTop: spacing.xs,
  },
  viewButton: {
    alignSelf: 'flex-end',
  },
});
