import React from 'react';
import { StyleSheet, TextInput, View } from 'react-native';
import { ActionButton, AppText, Card } from '../ui/Primitives';
import { useAppTheme } from '../../design/ThemeProvider';
import { radii, shadows, spacing, typography } from '../../theme';
import { strings } from '../../i18n/strings';
import { MemoryItem } from '../../memory/types';
import { memoryTypeLabel } from './MemoryCard';

interface MemoryDetailCardProps {
  selected: MemoryItem;
  editing: boolean;
  draft: string;
  saving: boolean;
  onDraftChange: (text: string) => void;
  onEdit: () => void;
  onSave: () => void;
  onCancel: () => void;
  onDelete: () => void;
}

export function MemoryDetailCard({
  selected,
  editing,
  draft,
  saving,
  onDraftChange,
  onEdit,
  onSave,
  onCancel,
  onDelete,
}: MemoryDetailCardProps) {
  const { colors } = useAppTheme();

  return (
    <Card
      style={[
        styles.card,
        {
          backgroundColor: colors.surface,
          borderColor: colors.primary,
        },
      ]}
      testID="memory-detail"
    >
      <View style={styles.headerRow}>
        <AppText style={[styles.sectionTitle, { color: colors.text }]}>
          {strings.memory.detailTitle}
        </AppText>
        <View
          style={[
            styles.typeBadge,
            { backgroundColor: colors.surfaceMuted },
          ]}
        >
          <AppText
            style={[styles.typeText, { color: colors.textMuted }]}
          >
            {memoryTypeLabel(selected.memory_type)}
          </AppText>
        </View>
      </View>

      {selected.supersedes_id ? (
        <View
          style={[
            styles.replacementBadge,
            { backgroundColor: colors.warningContainer },
          ]}
        >
          <AppText style={[styles.replacementText, { color: colors.warning }]}>
            {strings.memory.replacementNotice}
          </AppText>
        </View>
      ) : null}

      {editing ? (
        <TextInput
          accessibilityLabel={strings.memory.contentLabel}
          multiline
          numberOfLines={4}
          onChangeText={onDraftChange}
          style={[
            styles.input,
            styles.multilineInput,
            {
              color: colors.text,
              borderColor: colors.border,
              backgroundColor: colors.surfaceLow,
            },
          ]}
          testID="memory-edit-input"
          value={draft}
        />
      ) : (
        <AppText style={[styles.content, { color: colors.text }]}>
          {selected.content}
        </AppText>
      )}

      <View style={styles.actionsRow}>
        {editing ? (
          <>
            <ActionButton
              disabled={saving}
              label={saving ? strings.memory.saving : strings.memory.save}
              onPress={onSave}
              style={styles.actionHalf}
              testID="memory-save"
            />
            <ActionButton
              disabled={saving}
              label={strings.memory.cancel}
              onPress={onCancel}
              style={styles.actionHalf}
              testID="memory-cancel"
              variant="secondary"
            />
          </>
        ) : (
          <>
            <ActionButton
              label={strings.memory.edit}
              onPress={onEdit}
              style={styles.actionHalf}
              testID="memory-edit"
              variant="secondary"
            />
            <ActionButton
              disabled={saving}
              label={strings.memory.delete}
              onPress={onDelete}
              style={styles.actionHalf}
              testID="memory-delete"
              variant="quiet"
            />
          </>
        )}
      </View>
    </Card>
  );
}

const styles = StyleSheet.create({
  card: {
    borderRadius: radii.md,
    borderWidth: 1.5,
    gap: spacing.sm,
    marginTop: spacing.md,
    padding: spacing.md,
    ...shadows.md,
  },
  headerRow: {
    alignItems: 'center',
    flexDirection: 'row',
    justifyContent: 'space-between',
  },
  sectionTitle: {
    fontSize: typography.body,
    fontWeight: '700',
  },
  typeBadge: {
    borderRadius: radii.pill,
    paddingHorizontal: spacing.sm,
    paddingVertical: 2,
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
    marginVertical: spacing.xs,
  },
  input: {
    borderRadius: radii.sm,
    borderWidth: 1,
    fontSize: typography.body,
    paddingHorizontal: spacing.md,
    paddingVertical: spacing.sm,
  },
  multilineInput: {
    minHeight: 100,
    textAlignVertical: 'top',
  },
  actionsRow: {
    flexDirection: 'row',
    gap: spacing.sm,
    marginTop: spacing.xs,
  },
  actionHalf: {
    flex: 1,
  },
});
