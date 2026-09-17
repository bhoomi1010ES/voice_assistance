import React from 'react';
import { StyleSheet, TextInput, View } from 'react-native';
import { ActionButton, AppText, Card } from '../ui/Primitives';
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

  return (
    <Card
      style={[
        styles.card,
        {
          backgroundColor: colors.surface,
          borderColor: colors.border,
        },
      ]}
      testID="memory-create-card"
    >
      <AppText style={[styles.title, { color: colors.text }]}>
        {strings.memory.createTitle}
      </AppText>

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
            styles.multilineInput,
            {
              color: colors.text,
              borderColor: colors.border,
              backgroundColor: colors.surfaceLow,
            },
          ]}
          testID="memory-create-input"
          value={newContent}
        />
      </View>

      <ActionButton
        disabled={!enabled || saving || !newContent.trim()}
        label={saving ? strings.memory.creating : strings.memory.create}
        onPress={onSave}
        style={styles.actionButton}
        testID="memory-create"
        variant="primary"
      />
    </Card>
  );
}

const styles = StyleSheet.create({
  card: {
    borderRadius: radii.md,
    borderWidth: 1,
    gap: spacing.sm,
    padding: spacing.md,
    ...shadows.sm,
  },
  title: {
    fontSize: typography.body,
    fontWeight: '700',
  },
  inputContainer: {
    marginTop: spacing.xs,
  },
  input: {
    borderRadius: radii.sm,
    borderWidth: 1,
    fontSize: typography.body,
    paddingHorizontal: spacing.md,
    paddingVertical: spacing.sm,
  },
  multilineInput: {
    minHeight: 80,
    textAlignVertical: 'top',
  },
  actionButton: {
    marginTop: spacing.xs,
  },
});
