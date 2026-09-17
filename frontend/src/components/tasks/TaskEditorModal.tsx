import React from 'react';
import {
  KeyboardAvoidingView,
  Modal,
  Platform,
  Pressable,
  ScrollView,
  StyleSheet,
  TextInput,
  View,
} from 'react-native';
import { ActionButton, AppText, Heading, Screen } from '../ui/Primitives';
import { useAppTheme } from '../../design/ThemeProvider';
import { radii, spacing, typography } from '../../theme';
import { strings } from '../../i18n/strings';
import { dateInputForOffset, schedulePreview } from '../../tasks/scheduling';
import { TaskPriority } from '../../tasks/types';

export type TaskDraft = {
  title: string;
  description: string;
  date: string;
  time: string;
  timezone: string;
  priority: TaskPriority;
};

interface TaskEditorModalProps {
  visible: boolean;
  mode: 'create' | 'edit';
  draft: TaskDraft;
  saving: boolean;
  onChange: (draft: TaskDraft) => void;
  onClose: () => void;
  onSave: () => void;
}

export function TaskEditorModal({
  visible,
  mode,
  draft,
  saving,
  onChange,
  onClose,
  onSave,
}: TaskEditorModalProps) {
  const { colors } = useAppTheme();

  return (
    <Modal animationType="slide" onRequestClose={onClose} visible={visible}>
      <Screen>
        <KeyboardAvoidingView
          behavior={Platform.OS === 'ios' ? 'padding' : undefined}
          style={styles.flex}
        >
          <ScrollView
            contentContainerStyle={styles.scrollContent}
            keyboardShouldPersistTaps="handled"
          >
            <View style={styles.header}>
              <Heading>
                {mode === 'create'
                  ? strings.tasks.createTask
                  : strings.tasks.editTask}
              </Heading>
              <AppText style={[styles.subtitle, { color: colors.textMuted }]}>
                {strings.tasks.body}
              </AppText>
            </View>

            {/* Title Field */}
            <View style={styles.field}>
              <AppText style={[styles.fieldLabel, { color: colors.text }]}>
                {strings.tasks.titleLabel} *
              </AppText>
              <TextInput
                accessibilityLabel={strings.tasks.titleLabel}
                onChangeText={title => onChange({ ...draft, title })}
                placeholder="What needs to be done?"
                placeholderTextColor={colors.textSubtle}
                style={[
                  styles.input,
                  {
                    color: colors.text,
                    borderColor: colors.border,
                    backgroundColor: colors.surface,
                  },
                ]}
                testID="todo-title-input"
                value={draft.title}
              />
            </View>

            {/* Description Field */}
            <View style={styles.field}>
              <AppText style={[styles.fieldLabel, { color: colors.text }]}>
                {strings.tasks.descriptionLabel}
              </AppText>
              <TextInput
                accessibilityLabel={strings.tasks.descriptionLabel}
                multiline
                numberOfLines={3}
                onChangeText={description =>
                  onChange({ ...draft, description })
                }
                placeholder="Add additional notes or details (optional)"
                placeholderTextColor={colors.textSubtle}
                style={[
                  styles.input,
                  styles.multilineInput,
                  {
                    color: colors.text,
                    borderColor: colors.border,
                    backgroundColor: colors.surface,
                  },
                ]}
                testID="todo-description-input"
                value={draft.description}
              />
            </View>

            {/* Priority Selector */}
            <View style={styles.field}>
              <AppText style={[styles.fieldLabel, { color: colors.text }]}>
                Priority
              </AppText>
              <View style={styles.priorityRow}>
                {(['low', 'normal', 'high', 'urgent'] as const).map(p => (
                  <Pressable
                    key={p}
                    accessibilityRole="button"
                    accessibilityState={{ selected: draft.priority === p }}
                    onPress={() => onChange({ ...draft, priority: p })}
                    style={[
                      styles.priorityPill,
                      {
                        borderColor:
                          draft.priority === p ? colors.primary : colors.border,
                        backgroundColor:
                          draft.priority === p
                            ? colors.primaryContainer
                            : colors.surface,
                      },
                    ]}
                  >
                    <AppText
                      style={[
                        styles.priorityPillText,
                        {
                          color:
                            draft.priority === p
                              ? colors.onPrimaryContainer
                              : colors.textMuted,
                          fontWeight: draft.priority === p ? '700' : '500',
                          textTransform: 'capitalize',
                        },
                      ]}
                    >
                      {p}
                    </AppText>
                  </Pressable>
                ))}
              </View>
            </View>

            {/* Date & Time Fields */}
            <View style={styles.field}>
              <AppText style={[styles.fieldLabel, { color: colors.text }]}>
                {strings.tasks.dateLabel}
              </AppText>
              <TextInput
                accessibilityLabel={strings.tasks.dateLabel}
                onChangeText={date => onChange({ ...draft, date })}
                placeholder="YYYY-MM-DD"
                placeholderTextColor={colors.textSubtle}
                style={[
                  styles.input,
                  {
                    color: colors.text,
                    borderColor: colors.border,
                    backgroundColor: colors.surface,
                  },
                ]}
                testID="task-date-input"
                value={draft.date}
              />
              <View style={styles.quickDateRow}>
                <ActionButton
                  label={strings.tasks.tomorrow}
                  onPress={() =>
                    onChange({ ...draft, date: dateInputForOffset(1) })
                  }
                  testID="task-tomorrow"
                  variant="secondary"
                />
              </View>
            </View>

            <View style={styles.field}>
              <AppText style={[styles.fieldLabel, { color: colors.text }]}>
                {strings.tasks.timeLabel}
              </AppText>
              <TextInput
                accessibilityLabel={strings.tasks.timeLabel}
                onChangeText={time => onChange({ ...draft, time })}
                placeholder="HH:mm"
                placeholderTextColor={colors.textSubtle}
                style={[
                  styles.input,
                  {
                    color: colors.text,
                    borderColor: colors.border,
                    backgroundColor: colors.surface,
                  },
                ]}
                testID="task-time-input"
                value={draft.time}
              />
            </View>

            <View style={styles.field}>
              <AppText style={[styles.fieldLabel, { color: colors.text }]}>
                {strings.tasks.timezoneLabel}
              </AppText>
              <TextInput
                accessibilityLabel={strings.tasks.timezoneLabel}
                onChangeText={timezone => onChange({ ...draft, timezone })}
                placeholder="e.g. Asia/Kolkata, UTC"
                placeholderTextColor={colors.textSubtle}
                style={[
                  styles.input,
                  {
                    color: colors.text,
                    borderColor: colors.border,
                    backgroundColor: colors.surface,
                  },
                ]}
                testID="task-timezone-input"
                value={draft.timezone}
              />
            </View>

            {/* Schedule Preview Banner */}
            <View
              style={[
                styles.previewContainer,
                {
                  backgroundColor: colors.surfaceLow,
                  borderColor: colors.borderSubtle,
                },
              ]}
            >
              <AppText
                style={[
                  styles.previewLabel,
                  { color: colors.primary, fontWeight: '700' },
                ]}
              >
                {strings.tasks.preview}
              </AppText>
              <AppText
                style={[styles.previewValue, { color: colors.text }]}
                testID="todo-schedule-preview"
              >
                {schedulePreview(draft.date, draft.time, draft.timezone)}
              </AppText>
            </View>

            {/* Action Buttons */}
            <View style={styles.modalActions}>
              <ActionButton
                label={strings.tasks.cancel}
                onPress={onClose}
                variant="quiet"
              />
              <ActionButton
                disabled={saving}
                label={saving ? strings.tasks.saving : strings.tasks.save}
                onPress={onSave}
                testID="todo-save"
              />
            </View>
          </ScrollView>
        </KeyboardAvoidingView>
      </Screen>
    </Modal>
  );
}

const styles = StyleSheet.create({
  flex: {
    flex: 1,
  },
  scrollContent: {
    gap: spacing.md,
    paddingBottom: spacing.xxl,
  },
  header: {
    gap: spacing.xs,
    marginBottom: spacing.xs,
  },
  subtitle: {
    fontSize: typography.caption,
  },
  field: {
    gap: spacing.xs,
  },
  fieldLabel: {
    fontSize: typography.body,
    fontWeight: '600',
  },
  input: {
    borderRadius: radii.sm,
    borderWidth: 1,
    fontSize: typography.body,
    minHeight: 48,
    paddingHorizontal: spacing.md,
    paddingVertical: spacing.sm,
  },
  multilineInput: {
    minHeight: 80,
    textAlignVertical: 'top',
  },
  priorityRow: {
    flexDirection: 'row',
    gap: spacing.xs,
  },
  priorityPill: {
    alignItems: 'center',
    borderRadius: radii.pill,
    borderWidth: 1,
    flex: 1,
    justifyContent: 'center',
    minHeight: 40,
    paddingVertical: spacing.xs,
  },
  priorityPillText: {
    fontSize: typography.caption,
  },
  quickDateRow: {
    alignItems: 'flex-start',
    marginTop: spacing.xs,
  },
  previewContainer: {
    borderRadius: radii.sm,
    borderWidth: 1,
    gap: spacing.xs,
    padding: spacing.md,
  },
  previewLabel: {
    fontSize: typography.caption,
    letterSpacing: 0.5,
    textTransform: 'uppercase',
  },
  previewValue: {
    fontSize: typography.body,
    fontWeight: '500',
  },
  modalActions: {
    flexDirection: 'row',
    flexWrap: 'wrap',
    gap: spacing.sm,
    justifyContent: 'flex-end',
    marginTop: spacing.md,
  },
});
