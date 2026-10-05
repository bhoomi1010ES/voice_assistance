import React from 'react';
import { Pressable, StyleSheet, View } from 'react-native';
import { ActionButton, AppText, Card } from '../ui/Primitives';
import { useAppTheme } from '../../design/ThemeProvider';
import { radii, shadows, spacing, typography } from '../../theme';
import { strings } from '../../i18n/strings';
import { KnowledgeMode } from '../../memory/types';

interface MemorySettingsCardProps {
  enabled: boolean;
  saving: boolean;
  loading: boolean;
  knowledgeMode: KnowledgeMode;
  okfAvailable: boolean;
  onKnowledgeModeChange: (mode: KnowledgeMode) => void;
  onToggle: () => void;
}

export function MemorySettingsCard({
  enabled,
  saving,
  loading,
  knowledgeMode,
  okfAvailable,
  onKnowledgeModeChange,
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

      <View style={[styles.modeSection, { borderTopColor: colors.border }]}>
        <AppText style={[styles.modeTitle, { color: colors.text }]}>
          {strings.memory.knowledgeMode}
        </AppText>
        <AppText style={[styles.modeDescription, { color: colors.textMuted }]}>
          {strings.memory.knowledgeModeDescription}
        </AppText>
        <Pressable
          accessibilityLabel={strings.memory.hybridRag}
          accessibilityRole="radio"
          accessibilityState={{
            selected: knowledgeMode === 'rag',
            disabled: loading || saving,
          }}
          disabled={loading || saving}
          onPress={() => onKnowledgeModeChange('rag')}
          style={({ pressed }) => [
            styles.modeOption,
            { opacity: pressed ? 0.72 : 1 },
          ]}
          testID="knowledge-mode-rag"
        >
          <View style={[styles.radio, { borderColor: colors.accent }]}>
            {knowledgeMode === 'rag' ? (
              <View
                style={[
                  styles.radioSelected,
                  { backgroundColor: colors.accent },
                ]}
              />
            ) : null}
          </View>
          <View style={styles.modeCopy}>
            <AppText style={[styles.optionTitle, { color: colors.text }]}>
              {strings.memory.hybridRag}
            </AppText>
            <AppText
              style={[styles.optionDescription, { color: colors.textMuted }]}
            >
              {strings.memory.hybridRagDescription}
            </AppText>
          </View>
        </Pressable>
        <Pressable
          accessibilityLabel={strings.memory.okf}
          accessibilityRole="radio"
          accessibilityState={{
            selected: knowledgeMode === 'okf',
            disabled: loading || saving || !okfAvailable,
          }}
          disabled={loading || saving || !okfAvailable}
          onPress={() => onKnowledgeModeChange('okf')}
          style={({ pressed }) => [
            styles.modeOption,
            { opacity: !okfAvailable ? 0.5 : pressed ? 0.72 : 1 },
          ]}
          testID="knowledge-mode-okf"
        >
          <View style={[styles.radio, { borderColor: colors.accent }]}>
            {knowledgeMode === 'okf' ? (
              <View
                style={[
                  styles.radioSelected,
                  { backgroundColor: colors.accent },
                ]}
              />
            ) : null}
          </View>
          <View style={styles.modeCopy}>
            <AppText style={[styles.optionTitle, { color: colors.text }]}>
              {strings.memory.okf}
            </AppText>
            <AppText
              style={[styles.optionDescription, { color: colors.textMuted }]}
            >
              {okfAvailable
                ? strings.memory.okfDescription
                : strings.memory.okfUnavailable}
            </AppText>
          </View>
        </Pressable>
      </View>
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
  modeSection: {
    borderTopWidth: StyleSheet.hairlineWidth,
    gap: spacing.sm,
    marginTop: spacing.xs,
    paddingTop: spacing.md,
  },
  modeTitle: {
    fontSize: typography.body,
    fontWeight: '700',
  },
  modeDescription: {
    fontSize: typography.caption,
    lineHeight: 20,
  },
  modeOption: {
    alignItems: 'center',
    flexDirection: 'row',
    gap: spacing.sm,
    minHeight: 54,
    paddingVertical: spacing.xs,
  },
  radio: {
    alignItems: 'center',
    borderRadius: 99,
    borderWidth: 2,
    height: 22,
    justifyContent: 'center',
    width: 22,
  },
  radioSelected: {
    borderRadius: 99,
    height: 10,
    width: 10,
  },
  modeCopy: {
    flex: 1,
    gap: 2,
  },
  optionTitle: {
    fontSize: typography.body,
    fontWeight: '600',
  },
  optionDescription: {
    fontSize: typography.caption,
    lineHeight: 18,
  },
});
