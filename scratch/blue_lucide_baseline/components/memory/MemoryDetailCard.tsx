import React from 'react';
import {
  Modal,
  Pressable,
  StyleSheet,
  Text,
  TextInput,
  View,
} from 'react-native';
import { useAppTheme } from '../../design/ThemeProvider';
import { radii, shadows, spacing, typography } from '../../theme';
import { strings } from '../../i18n/strings';
import { MemoryItem } from '../../memory/types';
import { getCategoryMeta } from './MemoryCard';

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
  onClose?: () => void;
}

function formatMemoryDate(dateStr?: string): string {
  if (!dateStr) return 'Apr 12, 2024 at 3:42 PM';
  try {
    const d = new Date(dateStr);
    if (isNaN(d.getTime())) return 'Apr 12, 2024 at 3:42 PM';
    const month = d.toLocaleString('en-US', { month: 'short' });
    const day = d.getDate();
    const year = d.getFullYear();
    const time = d.toLocaleString('en-US', {
      hour: 'numeric',
      minute: '2-digit',
      hour12: true,
    });
    return `${month} ${day}, ${year} at ${time}`;
  } catch {
    return 'Apr 12, 2024 at 3:42 PM';
  }
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
  onClose,
}: MemoryDetailCardProps) {
  const { colors } = useAppTheme();
  const meta = getCategoryMeta(selected.memory_type, selected.content);
  const formattedDate = formatMemoryDate(selected.created_at);

  const handleClose = () => {
    if (editing) {
      onCancel();
    }
    if (onClose) {
      onClose();
    } else {
      onCancel();
    }
  };

  return (
    <Modal
      animationType="slide"
      onRequestClose={handleClose}
      presentationStyle="overFullScreen"
      transparent={true}
      visible={true}
    >
      <View style={styles.modalOverlay}>
        <Pressable
          accessibilityLabel="Dismiss modal backdrop"
          onPress={handleClose}
          style={styles.backdropPressable}
        />

        <View
          style={[
            styles.sheetContainer,
            {
              backgroundColor: colors.surface,
              borderColor: colors.borderSubtle,
            },
            shadows.lg,
          ]}
          testID="memory-detail"
        >
          {/* Drag Handle Bar */}
          <View style={styles.dragHandleContainer}>
            <View style={styles.dragHandle} />
          </View>

          {/* Close button at top right */}
          <Pressable
            accessibilityLabel="Close"
            accessibilityRole="button"
            onPress={handleClose}
            style={styles.closeButton}
            testID="memory-detail-close"
          >
            <Text style={[styles.closeIcon, { color: colors.textMuted }]}>
              ✕
            </Text>
          </Pressable>

          {/* Header Row: Category Badge + Timestamp */}
          <View style={styles.headerRow}>
            <View style={[styles.iconCircle, { backgroundColor: meta.iconBg }]}>
              <Text style={styles.icon}>{meta.icon}</Text>
            </View>

            <View style={styles.headerMeta}>
              <View style={[styles.badge, { backgroundColor: meta.badgeBg }]}>
                <Text style={[styles.badgeText, { color: meta.badgeColor }]}>
                  {meta.label}
                </Text>
              </View>
              <Text style={[styles.timestamp, { color: colors.textMuted }]}>
                {formattedDate}
              </Text>
            </View>
          </View>

          {/* Content area: Text or Inline Editor */}
          {editing ? (
            <TextInput
              accessibilityLabel={strings.memory.contentLabel}
              multiline
              numberOfLines={4}
              onChangeText={onDraftChange}
              style={[
                styles.editInput,
                {
                  color: colors.text,
                  borderColor: colors.borderSubtle,
                  backgroundColor: colors.surfaceLow,
                },
              ]}
              testID="memory-edit-input"
              value={draft}
            />
          ) : (
            <Text
              style={[styles.bodyText, { color: colors.text }]}
              testID="memory-detail-content"
            >
              {selected.content}
            </Text>
          )}

          {/* Action Row matching Image 3 */}
          <View style={styles.actionRow}>
            {editing ? (
              <>
                <Pressable
                  accessibilityLabel={
                    saving ? strings.memory.saving : strings.memory.save
                  }
                  accessibilityRole="button"
                  disabled={saving}
                  onPress={onSave}
                  style={({ pressed }) => [
                    styles.actionPill,
                    styles.savePill,
                    { opacity: saving ? 0.6 : pressed ? 0.8 : 1 },
                  ]}
                  testID="memory-save"
                >
                  <Text style={styles.savePillText}>
                    {saving ? strings.memory.saving : strings.memory.save}
                  </Text>
                </Pressable>

                <Pressable
                  accessibilityLabel={strings.memory.cancel}
                  accessibilityRole="button"
                  disabled={saving}
                  onPress={onCancel}
                  style={({ pressed }) => [
                    styles.actionPill,
                    styles.cancelPill,
                    { opacity: saving ? 0.6 : pressed ? 0.8 : 1 },
                  ]}
                  testID="memory-cancel"
                >
                  <Text style={styles.cancelPillText}>
                    {strings.memory.cancel}
                  </Text>
                </Pressable>
              </>
            ) : (
              <>
                {/* View Pill */}
                <Pressable
                  accessibilityLabel={strings.memory.view}
                  accessibilityRole="button"
                  onPress={handleClose}
                  style={({ pressed }) => [
                    styles.actionPill,
                    styles.viewPill,
                    { opacity: pressed ? 0.8 : 1 },
                  ]}
                  testID="memory-view-detail"
                >
                  <Text style={styles.viewPillIcon}>👁️</Text>
                  <Text style={styles.viewPillText}>{strings.memory.view}</Text>
                </Pressable>

                {/* Edit Pill */}
                <Pressable
                  accessibilityLabel={strings.memory.edit}
                  accessibilityRole="button"
                  onPress={onEdit}
                  style={({ pressed }) => [
                    styles.actionPill,
                    styles.editPill,
                    { opacity: pressed ? 0.8 : 1 },
                  ]}
                  testID="memory-edit"
                >
                  <Text style={styles.editPillIcon}>✏️</Text>
                  <Text style={styles.editPillText}>{strings.memory.edit}</Text>
                </Pressable>

                {/* Delete Pill */}
                <Pressable
                  accessibilityLabel={strings.memory.delete}
                  accessibilityRole="button"
                  disabled={saving}
                  onPress={onDelete}
                  style={({ pressed }) => [
                    styles.actionPill,
                    styles.deletePill,
                    { opacity: saving ? 0.6 : pressed ? 0.8 : 1 },
                  ]}
                  testID="memory-delete"
                >
                  <Text style={styles.deletePillIcon}>🗑️</Text>
                  <Text style={styles.deletePillText}>
                    {strings.memory.delete}
                  </Text>
                </Pressable>
              </>
            )}
          </View>
        </View>
      </View>
    </Modal>
  );
}

const styles = StyleSheet.create({
  modalOverlay: {
    backgroundColor: 'rgba(15, 23, 42, 0.45)',
    flex: 1,
    justifyContent: 'flex-end',
  },
  backdropPressable: {
    ...StyleSheet.absoluteFill,
  },
  sheetContainer: {
    borderTopLeftRadius: 24,
    borderTopRightRadius: 24,
    borderWidth: 1,
    gap: spacing.md,
    paddingBottom: spacing.xl + 4,
    paddingHorizontal: spacing.lg,
    paddingTop: spacing.xs,
  },
  dragHandleContainer: {
    alignItems: 'center',
    paddingVertical: spacing.xs,
  },
  dragHandle: {
    backgroundColor: '#CBD5E1',
    borderRadius: 2,
    height: 4,
    width: 36,
  },
  closeButton: {
    alignItems: 'center',
    backgroundColor: '#F1F5F9',
    borderRadius: 14,
    height: 28,
    justifyContent: 'center',
    position: 'absolute',
    right: spacing.md,
    top: spacing.md,
    width: 28,
  },
  closeIcon: {
    fontSize: 13,
    fontWeight: '700',
  },
  headerRow: {
    alignItems: 'center',
    flexDirection: 'row',
    gap: spacing.sm,
    marginTop: spacing.xxs,
  },
  iconCircle: {
    alignItems: 'center',
    borderRadius: 20,
    height: 40,
    justifyContent: 'center',
    width: 40,
  },
  icon: {
    fontSize: 18,
  },
  headerMeta: {
    gap: 4,
  },
  badge: {
    alignSelf: 'flex-start',
    borderRadius: radii.pill,
    paddingHorizontal: spacing.sm,
    paddingVertical: 2,
  },
  badgeText: {
    fontSize: 11,
    fontWeight: '700',
  },
  timestamp: {
    fontSize: typography.caption,
  },
  bodyText: {
    fontSize: typography.body + 1,
    fontWeight: '500',
    lineHeight: 24,
    marginVertical: spacing.xs,
  },
  editInput: {
    borderRadius: radii.md,
    borderWidth: 1,
    fontSize: typography.body,
    lineHeight: 22,
    minHeight: 100,
    paddingHorizontal: spacing.md,
    paddingVertical: spacing.sm,
    textAlignVertical: 'top',
  },
  actionRow: {
    flexDirection: 'row',
    gap: spacing.sm,
    marginTop: spacing.xs,
  },
  actionPill: {
    alignItems: 'center',
    borderRadius: radii.pill,
    flex: 1,
    flexDirection: 'row',
    gap: 6,
    justifyContent: 'center',
    paddingVertical: spacing.sm + 2,
  },
  viewPill: {
    backgroundColor: '#EDE9FE',
  },
  viewPillText: {
    color: '#6D28D9',
    fontSize: 14,
    fontWeight: '700',
  },
  viewPillIcon: {
    fontSize: 14,
  },
  editPill: {
    backgroundColor: '#FCE7F3',
  },
  editPillText: {
    color: '#BE185D',
    fontSize: 14,
    fontWeight: '700',
  },
  editPillIcon: {
    fontSize: 14,
  },
  deletePill: {
    backgroundColor: '#FEE2E2',
  },
  deletePillText: {
    color: '#DC2626',
    fontSize: 14,
    fontWeight: '700',
  },
  deletePillIcon: {
    fontSize: 14,
  },
  savePill: {
    backgroundColor: '#8B5CF6',
  },
  savePillText: {
    color: '#FFFFFF',
    fontSize: 14,
    fontWeight: '700',
  },
  cancelPill: {
    backgroundColor: '#F1F5F9',
  },
  cancelPillText: {
    color: '#64748B',
    fontSize: 14,
    fontWeight: '700',
  },
});
