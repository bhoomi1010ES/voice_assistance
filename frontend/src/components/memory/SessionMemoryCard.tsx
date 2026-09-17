import React from 'react';
import { StyleSheet, View } from 'react-native';
import { ActionButton, AppText, Card } from '../ui/Primitives';
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
    <Card
      style={[
        styles.card,
        {
          backgroundColor: sessionExcluded
            ? colors.surfaceLow
            : colors.surface,
          borderColor: sessionExcluded ? colors.warning : colors.border,
        },
      ]}
      testID="memory-session-card"
    >
      <View style={styles.headerRow}>
        <View
          style={[
            styles.iconContainer,
            {
              backgroundColor: sessionExcluded
                ? colors.warningContainer
                : colors.secondaryContainer,
            },
          ]}
        >
          <AppText style={styles.icon}>
            {sessionExcluded ? '🔒' : '🎙'}
          </AppText>
        </View>

        <View style={styles.titleContainer}>
          <View style={styles.titleRow}>
            <AppText style={[styles.title, { color: colors.text }]}>
              {strings.memory.sessionTitle}
            </AppText>
            <View
              style={[
                styles.badge,
                {
                  backgroundColor: sessionExcluded
                    ? colors.warningContainer
                    : colors.surfaceMuted,
                },
              ]}
            >
              <AppText
                style={[
                  styles.badgeText,
                  {
                    color: sessionExcluded
                      ? colors.warning
                      : colors.textMuted,
                  },
                ]}
              >
                {sessionExcluded ? 'Incognito' : 'Active Session'}
              </AppText>
            </View>
          </View>
          <AppText style={[styles.description, { color: colors.textMuted }]}>
            {strings.memory.sessionBody}
          </AppText>
        </View>
      </View>

      <ActionButton
        disabled={excluding}
        label={
          sessionExcluded
            ? strings.memory.includeSession
            : strings.memory.excludeSession
        }
        onPress={onToggle}
        style={styles.actionButton}
        testID="memory-session-exclusion"
        variant="secondary"
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
  badge: {
    borderRadius: radii.pill,
    paddingHorizontal: spacing.sm,
    paddingVertical: 2,
  },
  badgeText: {
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
