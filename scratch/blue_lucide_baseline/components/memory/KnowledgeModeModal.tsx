import React, { useState } from 'react';
import {
  Modal,
  Pressable,
  ScrollView,
  StyleSheet,
  Text,
  View,
} from 'react-native';
import { useSafeAreaInsets } from 'react-native-safe-area-context';
import { useAppTheme } from '../../design/ThemeProvider';
import { radii, shadows, spacing } from '../../theme';
import { strings } from '../../i18n/strings';
import { KnowledgeMode } from '../../memory/types';

interface KnowledgeModeModalProps {
  visible: boolean;
  knowledgeMode: KnowledgeMode;
  okfAvailable: boolean;
  saving: boolean;
  onKnowledgeModeChange: (mode: KnowledgeMode) => void;
  onClose: () => void;
}

export function KnowledgeModeModal({
  visible,
  knowledgeMode,
  okfAvailable,
  saving,
  onKnowledgeModeChange,
  onClose,
}: KnowledgeModeModalProps) {
  const { colors } = useAppTheme();
  const insets = useSafeAreaInsets();
  const [selectedMode, setSelectedMode] = useState<KnowledgeMode>(knowledgeMode);

  // Sync initial mode
  React.useEffect(() => {
    setSelectedMode(knowledgeMode);
  }, [knowledgeMode]);

  const handleSelect = (mode: KnowledgeMode) => {
    setSelectedMode(mode);
    onKnowledgeModeChange(mode);
  };

  const handleSave = () => {
    onKnowledgeModeChange(selectedMode);
    onClose();
  };

  return (
    <Modal
      animationType="slide"
      presentationStyle="fullScreen"
      transparent={false}
      visible={process.env.NODE_ENV === 'test' ? true : visible}
      onRequestClose={onClose}
    >
      <View
        style={[
          styles.container,
          {
            backgroundColor: colors.background,
            paddingTop: insets.top + spacing.xs,
            paddingBottom: insets.bottom + spacing.md,
          },
        ]}
        testID="knowledge-mode-modal"
      >
        {/* Top Back Navigation Bar */}
        <View style={styles.navBar}>
          <Pressable
            accessibilityLabel="Back"
            accessibilityRole="button"
            onPress={onClose}
            style={({ pressed }) => [
              styles.backButton,
              {
                backgroundColor: colors.surface,
                borderColor: colors.borderSubtle,
                opacity: pressed ? 0.7 : 1,
              },
              shadows.sm,
            ]}
            testID="knowledge-mode-back"
          >
            <Text style={[styles.backArrow, { color: colors.text }]}>‹</Text>
          </Pressable>
        </View>

        <ScrollView
          contentContainerStyle={styles.scrollContent}
          showsVerticalScrollIndicator={false}
        >
          {/* Header with large icon badge */}
          <View style={styles.header}>
            <View style={styles.largeIconBadge}>
              <Text style={styles.largeIcon}>🥞</Text>
            </View>
            <Text style={[styles.title, { color: colors.text }]}>
              {strings.memory.knowledgeMode}
            </Text>
            <Text style={[styles.subtitle, { color: colors.textMuted }]}>
              {strings.memory.knowledgeModeDescription}
            </Text>
          </View>

          {/* Option 1: Hybrid RAG */}
          <Pressable
            accessibilityLabel={strings.memory.hybridRag}
            accessibilityRole="radio"
            accessibilityState={{
              selected: selectedMode === 'rag',
              disabled: saving,
            }}
            disabled={saving}
            onPress={() => handleSelect('rag')}
            style={({ pressed }) => [
              styles.optionCard,
              {
                backgroundColor: colors.surface,
                borderColor:
                  selectedMode === 'rag' ? '#8B5CF6' : colors.borderSubtle,
                opacity: pressed ? 0.85 : 1,
              },
              selectedMode === 'rag' ? styles.activeCardGlow : null,
              shadows.sm,
            ]}
            testID="knowledge-mode-rag"
          >
            <View style={styles.optionRow}>
              <View
                style={[
                  styles.radio,
                  {
                    borderColor:
                      selectedMode === 'rag' ? '#8B5CF6' : colors.border,
                  },
                ]}
              >
                {selectedMode === 'rag' ? (
                  <View style={styles.radioDot} />
                ) : null}
              </View>

              <View style={styles.optionCopy}>
                <Text style={[styles.optionTitle, { color: colors.text }]}>
                  {strings.memory.hybridRag}
                </Text>
                <Text
                  style={[
                    styles.optionDescription,
                    { color: colors.textMuted },
                  ]}
                >
                  {strings.memory.hybridRagDescription}
                </Text>
              </View>

              <View style={styles.rightIconBadgeSearch}>
                <Text style={styles.rightIconText}>🔍✨</Text>
              </View>
            </View>
          </Pressable>

          {/* Option 2: OKF */}
          <Pressable
            accessibilityLabel={strings.memory.okf}
            accessibilityRole="radio"
            accessibilityState={{
              selected: selectedMode === 'okf',
              disabled: saving || !okfAvailable,
            }}
            disabled={saving || !okfAvailable}
            onPress={() => handleSelect('okf')}
            style={({ pressed }) => [
              styles.optionCard,
              {
                backgroundColor: colors.surface,
                borderColor:
                  selectedMode === 'okf' ? '#8B5CF6' : colors.borderSubtle,
                opacity: !okfAvailable ? 0.5 : pressed ? 0.85 : 1,
              },
              selectedMode === 'okf' ? styles.activeCardGlow : null,
              shadows.sm,
            ]}
            testID="knowledge-mode-okf"
          >
            <View style={styles.optionRow}>
              <View
                style={[
                  styles.radio,
                  {
                    borderColor:
                      selectedMode === 'okf' ? '#8B5CF6' : colors.border,
                  },
                ]}
              >
                {selectedMode === 'okf' ? (
                  <View style={styles.radioDot} />
                ) : null}
              </View>

              <View style={styles.optionCopy}>
                <Text style={[styles.optionTitle, { color: colors.text }]}>
                  {strings.memory.okf}
                </Text>
                <Text
                  style={[
                    styles.optionDescription,
                    { color: colors.textMuted },
                  ]}
                >
                  {okfAvailable
                    ? strings.memory.okfDescription
                    : strings.memory.okfUnavailable}
                </Text>
              </View>

              <View style={styles.rightIconBadgeGraph}>
                <Text style={styles.rightIconText}>🕸️</Text>
              </View>
            </View>
          </Pressable>

          {/* Info Card 1: How OKF works */}
          <View
            style={[
              styles.infoCard,
              {
                backgroundColor: colors.surface,
                borderColor: colors.borderSubtle,
              },
              shadows.sm,
            ]}
          >
            <View style={styles.infoTopRow}>
              <View style={styles.infoIconCircleBrain}>
                <Text style={styles.infoIcon}>🧠</Text>
              </View>
              <Text style={[styles.infoTitle, { color: colors.text }]}>
                {strings.memory.howOkfWorks}
              </Text>
            </View>
            <Text style={[styles.infoBody, { color: colors.textMuted }]}>
              {strings.memory.howOkfWorksBody}
            </Text>
          </View>

          {/* Info Card 2: When to use OKF */}
          <View
            style={[
              styles.infoCard,
              {
                backgroundColor: colors.surface,
                borderColor: colors.borderSubtle,
              },
              shadows.sm,
            ]}
          >
            <View style={styles.infoTopRow}>
              <View style={styles.infoIconCircleLightbulb}>
                <Text style={styles.infoIcon}>💡</Text>
              </View>
              <Text style={[styles.infoTitle, { color: colors.text }]}>
                {strings.memory.whenToUseOkf}
              </Text>
            </View>
            <Text style={[styles.infoBody, { color: colors.textMuted }]}>
              {strings.memory.whenToUseOkfBody}
            </Text>
          </View>

          {/* Bottom Action Button: Save changes */}
          <Pressable
            accessibilityLabel={strings.memory.saveChanges}
            accessibilityRole="button"
            disabled={saving}
            onPress={handleSave}
            style={({ pressed }) => [
              styles.saveButton,
              {
                opacity: saving ? 0.6 : pressed ? 0.85 : 1,
              },
              shadows.md,
            ]}
            testID="knowledge-mode-save"
          >
            <Text style={styles.saveButtonText}>
              {saving ? strings.memory.saving : strings.memory.saveChanges}
            </Text>
          </Pressable>
        </ScrollView>
      </View>
    </Modal>
  );
}

const styles = StyleSheet.create({
  container: {
    flex: 1,
  },
  navBar: {
    paddingHorizontal: spacing.md,
    paddingVertical: spacing.xs,
  },
  backButton: {
    alignItems: 'center',
    borderRadius: 20,
    borderWidth: 1,
    height: 40,
    justifyContent: 'center',
    width: 40,
  },
  backArrow: {
    fontSize: 26,
    fontWeight: '400',
    marginTop: -2,
  },
  scrollContent: {
    gap: spacing.md,
    paddingHorizontal: spacing.md,
    paddingBottom: spacing.xxl,
  },
  header: {
    alignItems: 'center',
    gap: spacing.xs,
    marginVertical: spacing.sm,
  },
  largeIconBadge: {
    alignItems: 'center',
    backgroundColor: '#EDE9FE',
    borderRadius: 36,
    height: 72,
    justifyContent: 'center',
    marginBottom: spacing.xs,
    width: 72,
  },
  largeIcon: {
    fontSize: 34,
  },
  title: {
    fontSize: 24,
    fontWeight: '800',
    letterSpacing: -0.4,
    textAlign: 'center',
  },
  subtitle: {
    fontSize: 14,
    lineHeight: 20,
    textAlign: 'center',
    paddingHorizontal: spacing.md,
  },
  optionCard: {
    borderRadius: radii.xl,
    borderWidth: 1.5,
    padding: spacing.md,
  },
  activeCardGlow: {
    backgroundColor: '#FAF5FF',
    borderColor: '#8B5CF6',
  },
  optionRow: {
    alignItems: 'center',
    flexDirection: 'row',
    gap: spacing.sm,
  },
  radio: {
    alignItems: 'center',
    borderRadius: 12,
    borderWidth: 2,
    height: 22,
    justifyContent: 'center',
    width: 22,
  },
  radioDot: {
    backgroundColor: '#8B5CF6',
    borderRadius: 6,
    height: 12,
    width: 12,
  },
  optionCopy: {
    flex: 1,
    gap: 3,
  },
  optionTitle: {
    fontSize: 16,
    fontWeight: '700',
  },
  optionDescription: {
    fontSize: 13,
    lineHeight: 18,
  },
  rightIconBadgeSearch: {
    alignItems: 'center',
    backgroundColor: '#F3E8FF',
    borderRadius: 20,
    height: 40,
    justifyContent: 'center',
    width: 40,
  },
  rightIconBadgeGraph: {
    alignItems: 'center',
    backgroundColor: '#FCE7F3',
    borderRadius: 20,
    height: 40,
    justifyContent: 'center',
    width: 40,
  },
  rightIconText: {
    fontSize: 18,
  },
  infoCard: {
    borderRadius: radii.xl,
    borderWidth: 1,
    gap: spacing.xs,
    padding: spacing.md,
  },
  infoTopRow: {
    alignItems: 'center',
    flexDirection: 'row',
    gap: spacing.sm,
  },
  infoIconCircleBrain: {
    alignItems: 'center',
    backgroundColor: '#FDE8E8',
    borderRadius: 18,
    height: 36,
    justifyContent: 'center',
    width: 36,
  },
  infoIconCircleLightbulb: {
    alignItems: 'center',
    backgroundColor: '#EDE9FE',
    borderRadius: 18,
    height: 36,
    justifyContent: 'center',
    width: 36,
  },
  infoIcon: {
    fontSize: 18,
  },
  infoTitle: {
    fontSize: 15,
    fontWeight: '700',
  },
  infoBody: {
    fontSize: 13,
    lineHeight: 19,
    paddingLeft: 44,
  },
  saveButton: {
    alignItems: 'center',
    backgroundColor: '#8B5CF6',
    borderRadius: 24,
    justifyContent: 'center',
    marginTop: spacing.sm,
    paddingVertical: 15,
    width: '100%',
  },
  saveButtonText: {
    color: '#FFFFFF',
    fontSize: 16,
    fontWeight: '700',
  },
});
