import React from 'react';
import { Pressable, StyleSheet, Text, View } from 'react-native';
import { useAppTheme } from '../../design/ThemeProvider';
import { radii, shadows, spacing } from '../../theme';
import { strings } from '../../i18n/strings';
import { KnowledgeMode } from '../../memory/types';

interface KnowledgeModeCardProps {
  knowledgeMode: KnowledgeMode;
  onPress: () => void;
}

export function KnowledgeModeCard({
  knowledgeMode,
  onPress,
}: KnowledgeModeCardProps) {
  const { colors } = useAppTheme();

  const isOkf = knowledgeMode === 'okf';

  return (
    <Pressable
      accessibilityLabel={strings.memory.knowledgeMode}
      accessibilityRole="button"
      onPress={onPress}
      style={({ pressed }) => [
        styles.card,
        {
          backgroundColor: colors.surface,
          borderColor: colors.borderSubtle,
          opacity: pressed ? 0.85 : 1,
        },
        shadows.sm,
      ]}
      testID="memory-knowledge-mode-card"
    >
      <View style={styles.contentRow}>
        <View style={styles.iconCircle}>
          <Text style={styles.icon}>🥞</Text>
        </View>

        <View style={styles.textContainer}>
          <Text style={[styles.title, { color: colors.text }]}>
            {strings.memory.knowledgeMode}
          </Text>
          <Text style={[styles.value, { color: colors.text }]}>
            {isOkf ? strings.memory.okf : strings.memory.hybridRag}
          </Text>
          <Text style={[styles.subtitle, { color: colors.textMuted }]}>
            {isOkf
              ? strings.memory.okfDescription
              : strings.memory.hybridRagDescription}
          </Text>
        </View>

        <View style={styles.chevronWrapper}>
          <Text style={[styles.chevron, { color: colors.textMuted }]}>›</Text>
        </View>
      </View>
    </Pressable>
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
    backgroundColor: '#EDE9FE',
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
    gap: 2,
  },
  title: {
    fontSize: 16,
    fontWeight: '700',
  },
  value: {
    fontSize: 15,
    fontWeight: '700',
    marginTop: 1,
  },
  subtitle: {
    fontSize: 13,
    lineHeight: 17,
    marginTop: 2,
  },
  chevronWrapper: {
    paddingLeft: spacing.xs,
  },
  chevron: {
    fontSize: 24,
    fontWeight: '300',
  },
});
