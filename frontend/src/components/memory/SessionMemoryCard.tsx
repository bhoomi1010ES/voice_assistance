import { GlyphIcon } from '../ui/AppIcon';
import React from 'react';
import { Pressable, StyleSheet, Text, View } from 'react-native';
import { AppText } from '../ui/Primitives';
import { useAppTheme } from '../../design/ThemeProvider';
import { radii, shadows, spacing, typography } from '../../theme';
import { strings } from '../../i18n/strings';

interface SessionMemoryCardProps {
  sessionExcluded: boolean;
  excluding: boolean;
  onToggle: () => void;
}

export function SessionMemoryCard({
  sessionExcluded,
  excluding,
  onToggle,
}: SessionMemoryCardProps) {
  const { colors } = useAppTheme();

  return (
    <View
      style={[
        styles.card,
        {
          backgroundColor: colors.surface,
          borderColor: sessionExcluded ? colors.warning : colors.borderSubtle,
        },
        shadows.sm,
      ]}
      testID="memory-session-card"
    >
      <View style={styles.contentRow}>
        <View style={styles.iconCircle}>
          <GlyphIcon glyph={sessionExcluded ? '🔒' : '👁️‍🗨️'} size={22} />
        </View>

        <View style={styles.textContainer}>
          <AppText style={[styles.title, { color: colors.text }]}>
            {strings.memory.sessionTitle}
          </AppText>
          <Text style={[styles.description, { color: colors.textMuted }]}>
            {strings.memory.sessionBody}
          </Text>
        </View>

        <Pressable
          accessibilityLabel={
            sessionExcluded
              ? strings.memory.includeSession
              : strings.memory.excludeSession
          }
          accessibilityRole="button"
          disabled={excluding}
          onPress={onToggle}
          style={({ pressed }) => [
            styles.actionButton,
            {
              backgroundColor: sessionExcluded
                ? colors.warningContainer
                : colors.surfaceLow,
              borderColor: sessionExcluded
                ? colors.warning
                : colors.borderSubtle,
              opacity: excluding ? 0.6 : pressed ? 0.8 : 1,
            },
          ]}
          testID="memory-session-exclusion"
        >
          <Text
            style={[
              styles.actionButtonText,
              {
                color: sessionExcluded ? colors.warning : colors.text,
              },
            ]}
          >
            {sessionExcluded
              ? strings.memory.includeSession
              : strings.memory.excludeSession}
          </Text>
        </Pressable>
      </View>
    </View>
  );
}

const styles = StyleSheet.create({
  card: {
    borderRadius: radii.xl,
    borderWidth: 1,
    padding: spacing.md,
  },
  contentRow: {
    alignItems: 'center',
    flexDirection: 'row',
    gap: spacing.sm,
  },
  iconCircle: {
    alignItems: 'center',
    backgroundColor: '#E3EFFF',
    borderRadius: 20,
    height: 40,
    justifyContent: 'center',
    width: 40,
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
  description: {
    fontSize: typography.caption,
    lineHeight: 16,
  },
  actionButton: {
    alignItems: 'center',
    borderRadius: radii.pill,
    borderWidth: 1,
    justifyContent: 'center',
    paddingHorizontal: spacing.sm + 2,
    paddingVertical: spacing.xs + 2,
  },
  actionButtonText: {
    fontSize: 12,
    fontWeight: '700',
  },
});
