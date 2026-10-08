import React from 'react';
import { Pressable, StyleSheet, Text, TextInput, View } from 'react-native';
import { AppText } from '../ui/Primitives';
import { useAppTheme } from '../../design/ThemeProvider';
import { radii, shadows, spacing, typography } from '../../theme';
import { strings } from '../../i18n/strings';

interface MemoryCreateCardProps {
  newContent: string;
  onContentChange: (text: string) => void;
  onSave: () => void;
  saving: boolean;
  enabled: boolean;
}

export function MemoryCreateCard({
  newContent,
  onContentChange,
  onSave,
  saving,
  enabled,
}: MemoryCreateCardProps) {
  const { colors } = useAppTheme();
  const isSubmitDisabled = !enabled || saving || !newContent.trim();

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
      testID="memory-create-card"
    >
      <View style={styles.headerRow}>
        <View style={styles.iconCircle}>
          <Text style={styles.icon}>✏️</Text>
        </View>
        <AppText style={[styles.title, { color: colors.text }]}>
          {strings.memory.createTitle}
        </AppText>
      </View>

      <View style={styles.inputContainer}>
        <TextInput
          accessibilityLabel={strings.memory.contentLabel}
          editable={enabled && !saving}
          multiline
          numberOfLines={3}
          onChangeText={onContentChange}
          placeholder={strings.memory.createPlaceholder}
          placeholderTextColor={colors.textSubtle}
          style={[
            styles.input,
            {
              color: colors.text,
              borderColor: colors.borderSubtle,
              backgroundColor: colors.surfaceLow,
            },
          ]}
          testID="memory-create-input"
          value={newContent}
        />
      </View>

      <Pressable
        accessibilityLabel={
          saving ? strings.memory.creating : strings.memory.create
        }
        accessibilityRole="button"
        disabled={isSubmitDisabled}
        onPress={onSave}
        style={({ pressed }) => [
          styles.saveButton,
          {
            backgroundColor: isSubmitDisabled ? colors.surfaceMuted : '#8B5CF6',
            opacity: isSubmitDisabled ? 0.6 : pressed ? 0.85 : 1,
          },
          shadows.sm,
        ]}
        testID="memory-create"
      >
        <Text
          style={[
            styles.saveButtonText,
            {
              color: isSubmitDisabled ? colors.textMuted : '#FFFFFF',
            },
          ]}
        >
          {saving ? strings.memory.creating : strings.memory.create}
        </Text>
      </Pressable>
    </View>
  );
}

const styles = StyleSheet.create({
  card: {
    borderRadius: radii.xl,
    borderWidth: 1,
    gap: spacing.sm,
    padding: spacing.md,
  },
  headerRow: {
    alignItems: 'center',
    flexDirection: 'row',
    gap: spacing.sm,
  },
  iconCircle: {
    alignItems: 'center',
    backgroundColor: '#FFE4E6',
    borderRadius: 18,
    height: 36,
    justifyContent: 'center',
    width: 36,
  },
  icon: {
    fontSize: 16,
  },
  title: {
    fontSize: typography.body,
    fontWeight: '700',
  },
  inputContainer: {
    marginTop: spacing.xxs,
  },
  input: {
    borderRadius: radii.md,
    borderWidth: 1,
    fontSize: typography.body,
    minHeight: 80,
    paddingHorizontal: spacing.md,
    paddingVertical: spacing.sm,
    textAlignVertical: 'top',
  },
  saveButton: {
    alignItems: 'center',
    borderRadius: radii.pill,
    justifyContent: 'center',
    marginTop: spacing.xs,
    paddingVertical: spacing.sm + 2,
  },
  saveButtonText: {
    fontSize: typography.body,
    fontWeight: '700',
  },
});

