import React from 'react';
import {
  KeyboardAvoidingView,
  Modal,
  Platform,
  Pressable,
  ScrollView,
  StyleSheet,
  View,
} from 'react-native';
import { AppText, Card, Screen } from '../ui/Primitives';
import { useAppTheme } from '../../design/ThemeProvider';
import { strings } from '../../i18n/strings';
import { dateInputForOffset, schedulePreview } from '../../tasks/scheduling';
import { Task, TaskPriority } from '../../tasks/types';
import {
  EditorField,
  EditorHeader,
  EditorSummary,
  ScheduleLine,
  TaskBadge,
  TaskButton,
  presentationStyles as ui,
} from './TaskPresentation';

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
  task?: Task;
  onDelete?: () => void;
}

export function TaskEditorModal({
  visible,
  mode,
  draft,
  saving,
  onChange,
  onClose,
  onSave,
  task,
  onDelete,
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
            contentContainerStyle={ui.scroll}
            keyboardShouldPersistTaps="handled"
          >
            <EditorHeader
              onClose={onClose}
              subtitle="Update task details and schedule"
              title={
                mode === 'create'
                  ? strings.tasks.createTask
                  : strings.tasks.editTask
              }
            />
            {mode === 'edit' && task ? (
              <EditorSummary title={task.title}>
                <ScheduleLine at={task.due_at} timezone={task.timezone} />
                <View style={ui.badges}>
                  <TaskBadge
                    label={`● ${task.priority}`}
                    tone={task.priority}
                  />
                  <TaskBadge
                    label={task.status.replace('_', ' ')}
                    tone={
                      task.status === 'completed'
                        ? 'completed'
                        : task.status === 'cancelled'
                        ? 'cancelled'
                        : 'pending'
                    }
                  />
                  {task.source_turn_id ? (
                    <TaskBadge label="🎙 Voice created" tone="voice" />
                  ) : null}
                </View>
              </EditorSummary>
            ) : null}
            <Card style={ui.formCard}>
              <EditorField
                accessibilityLabel={strings.tasks.titleLabel}
                label="Title *"
                onChangeText={title => onChange({ ...draft, title })}
                placeholder="What needs to be done?"
                testID="todo-title-input"
                value={draft.title}
              />
              <EditorField
                accessibilityLabel={strings.tasks.descriptionLabel}
                label="Description / notes"
                multiline
                numberOfLines={3}
                onChangeText={description =>
                  onChange({ ...draft, description })
                }
                placeholder="Add additional notes or details (optional)"
                testID="todo-description-input"
                value={draft.description}
              />
              <View style={ui.pairedFields}>
                <EditorField
                  accessibilityLabel={strings.tasks.dateLabel}
                  label="Date"
                  onChangeText={date => onChange({ ...draft, date })}
                  placeholder="YYYY-MM-DD"
                  testID="task-due-date-input"
                  value={draft.date}
                />
                <EditorField
                  accessibilityLabel={strings.tasks.timeLabel}
                  label="Time"
                  onChangeText={time => onChange({ ...draft, time })}
                  placeholder="HH:mm"
                  testID="task-time-input"
                  value={draft.time}
                />
              </View>
              <View style={styles.quickDate}>
                <TaskButton
                  label={strings.tasks.tomorrow}
                  onPress={() =>
                    onChange({ ...draft, date: dateInputForOffset(1) })
                  }
                  testID="task-tomorrow"
                  variant="secondary"
                />
              </View>
              <EditorField
                accessibilityLabel={strings.tasks.timezoneLabel}
                label="Time zone"
                onChangeText={timezone => onChange({ ...draft, timezone })}
                placeholder="e.g. Asia/Kolkata, UTC"
                testID="task-timezone-input"
                value={draft.timezone}
              />
              <View style={ui.field}>
                <AppText style={[ui.fieldLabel, { color: colors.textMuted }]}>
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
                        styles.priorityOption,
                        draft.priority === p
                          ? { borderColor: colors.primary }
                          : null,
                      ]}
                    >
                      <TaskBadge label={`● ${p}`} tone={p} />
                    </Pressable>
                  ))}
                </View>
              </View>
              {task?.source_turn_id ? (
                <View style={ui.badges}>
                  <AppText style={ui.fieldLabel}>Source</AppText>
                  <TaskBadge label="🎙 Voice created" tone="voice" />
                </View>
              ) : null}
              <View
                style={[ui.preview, { backgroundColor: colors.surfaceLow }]}
              >
                <AppText style={[ui.fieldLabel, { color: colors.primary }]}>
                  {strings.tasks.preview}
                </AppText>
                <AppText
                  style={styles.previewValue}
                  testID="todo-schedule-preview"
                >
                  {schedulePreview(draft.date, draft.time, draft.timezone)}
                </AppText>
              </View>
            </Card>
            <View style={ui.footer}>
              <TaskButton
                disabled={saving}
                label={
                  saving
                    ? strings.tasks.saving
                    : mode === 'edit'
                    ? 'Save changes'
                    : strings.tasks.save
                }
                onPress={onSave}
                testID="todo-save"
              />
              {mode === 'edit' && onDelete ? (
                <TaskButton
                  destructive
                  disabled={saving}
                  label="Delete task"
                  onPress={() => {
                    onClose();
                    onDelete();
                  }}
                  testID="todo-editor-delete"
                  variant="quiet"
                />
              ) : null}
              <TaskButton
                label={strings.tasks.cancel}
                onPress={onClose}
                variant="quiet"
              />
            </View>
          </ScrollView>
        </KeyboardAvoidingView>
      </Screen>
    </Modal>
  );
}

const styles = StyleSheet.create({
  flex: { flex: 1 },
  quickDate: { alignItems: 'flex-start', marginTop: -10 },
  priorityRow: { flexDirection: 'row', flexWrap: 'wrap', gap: 5 },
  priorityOption: {
    borderColor: 'transparent',
    borderRadius: 18,
    borderWidth: 1,
    padding: 2,
    minHeight: 44,
    justifyContent: 'center',
  },
  previewValue: { fontSize: 13, lineHeight: 20 },
});
