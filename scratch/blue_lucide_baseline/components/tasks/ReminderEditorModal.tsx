import React from 'react';
import {
  KeyboardAvoidingView,
  Modal,
  Platform,
  ScrollView,
  StyleSheet,
  View,
} from 'react-native';
import { AppText, Card, Screen } from '../ui/Primitives';
import { useAppTheme } from '../../design/ThemeProvider';
import { strings } from '../../i18n/strings';
import { dateInputForOffset, schedulePreview } from '../../tasks/scheduling';
import { Reminder } from '../../tasks/types';
import {
  EditorField,
  EditorHeader,
  EditorSummary,
  ScheduleLine,
  TaskBadge,
  TaskButton,
  presentationStyles as ui,
} from './TaskPresentation';

export type ReminderDraft = {
  title: string;
  body: string;
  date: string;
  time: string;
  timezone: string;
  recurrence: 'none' | 'daily' | 'weekly';
};

interface ReminderEditorModalProps {
  visible: boolean;
  mode: 'create' | 'edit';
  draft: ReminderDraft;
  saving: boolean;
  onChange: (draft: ReminderDraft) => void;
  onClose: () => void;
  onSave: () => void;
  reminder?: Reminder;
  onDelete?: () => void;
}

const RECURRENCE_OPTIONS = ['none', 'daily', 'weekly'] as const;

export function ReminderEditorModal({
  visible,
  mode,
  draft,
  saving,
  onChange,
  onClose,
  onSave,
  reminder,
  onDelete,
}: ReminderEditorModalProps) {
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
              subtitle="Schedule when the reminder appears"
              title={
                mode === 'create'
                  ? strings.tasks.createReminder
                  : strings.tasks.editReminder
              }
            />
            {mode === 'edit' && reminder ? (
              <EditorSummary reminder title={reminder.title}>
                <ScheduleLine
                  at={reminder.trigger_at}
                  timezone={reminder.timezone}
                />
                <View style={ui.badges}>
                  <TaskBadge
                    label={
                      reminder.status === 'sent' ? 'Delivered' : reminder.status
                    }
                    tone={reminder.status}
                  />
                  <TaskBadge
                    label={
                      reminder.delivery_channel === 'push'
                        ? '🔔 Push notification'
                        : reminder.delivery_channel
                    }
                    tone="voice"
                  />
                </View>
              </EditorSummary>
            ) : null}
            <Card style={ui.formCard}>
              <EditorField
                accessibilityLabel={strings.tasks.titleLabel}
                label="Reminder title *"
                onChangeText={title => onChange({ ...draft, title })}
                placeholder="What should we remind you about?"
                testID="reminder-title-input"
                value={draft.title}
              />
              <EditorField
                accessibilityLabel={strings.tasks.bodyLabel}
                label="Message"
                multiline
                numberOfLines={3}
                onChangeText={body => onChange({ ...draft, body })}
                placeholder="Additional reminder details (optional)"
                testID="reminder-body-input"
                value={draft.body}
              />
              <View style={ui.pairedFields}>
                <EditorField
                  accessibilityLabel={strings.tasks.dateLabel}
                  label="Date *"
                  onChangeText={date => onChange({ ...draft, date })}
                  placeholder="YYYY-MM-DD"
                  testID="reminder-date-input"
                  value={draft.date}
                />
                <EditorField
                  accessibilityLabel={strings.tasks.timeLabel}
                  label="Time *"
                  onChangeText={time => onChange({ ...draft, time })}
                  placeholder="HH:mm"
                  testID="reminder-time-input"
                  value={draft.time}
                />
              </View>
              <View style={styles.quickDate}>
                <TaskButton
                  label={strings.tasks.tomorrow}
                  onPress={() =>
                    onChange({ ...draft, date: dateInputForOffset(1) })
                  }
                  testID="reminder-tomorrow"
                  variant="secondary"
                />
              </View>
              <EditorField
                accessibilityLabel={strings.tasks.timezoneLabel}
                label="Time zone *"
                onChangeText={timezone => onChange({ ...draft, timezone })}
                placeholder="e.g. Asia/Kolkata, UTC"
                testID="reminder-timezone-input"
                value={draft.timezone}
              />
              <View style={ui.field}>
                <AppText style={[ui.fieldLabel, { color: colors.textMuted }]}>
                  Repeat
                </AppText>
                <View style={styles.recurrenceRow}>
                  {RECURRENCE_OPTIONS.map(choice => (
                    <TaskButton
                      key={choice}
                      accessibilityState={{
                        selected: draft.recurrence === choice,
                      }}
                      label={
                        choice === 'none'
                          ? 'Never'
                          : choice === 'daily'
                          ? 'Daily'
                          : 'Weekly'
                      }
                      onPress={() => onChange({ ...draft, recurrence: choice })}
                      style={styles.recurrenceOption}
                      testID={`recurrence-${choice}`}
                      variant={
                        draft.recurrence === choice ? 'primary' : 'secondary'
                      }
                    />
                  ))}
                </View>
              </View>
              <View style={ui.field}>
                <AppText style={[ui.fieldLabel, { color: colors.textMuted }]}>
                  Delivery type
                </AppText>
                <View style={ui.badges}>
                  <TaskBadge label="🔔 Push notification" tone="voice" />
                </View>
              </View>
              <View
                style={[ui.preview, { backgroundColor: colors.surfaceLow }]}
              >
                <AppText style={[ui.fieldLabel, { color: colors.primary }]}>
                  {strings.tasks.preview}
                </AppText>
                <AppText
                  style={styles.previewValue}
                  testID="reminder-schedule-preview"
                >
                  {schedulePreview(draft.date, draft.time, draft.timezone)}
                </AppText>
                <AppText
                  style={[styles.previewHelp, { color: colors.textMuted }]}
                >
                  {strings.tasks.serverValidation}
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
                    ? 'Save reminder'
                    : strings.tasks.save
                }
                onPress={onSave}
                testID="reminder-save"
              />
              {mode === 'edit' && onDelete ? (
                <TaskButton
                  destructive
                  disabled={saving}
                  label="Delete reminder"
                  onPress={() => {
                    onClose();
                    onDelete();
                  }}
                  testID="reminder-editor-delete"
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
  recurrenceRow: { flexDirection: 'row', flexWrap: 'wrap', gap: 6 },
  recurrenceOption: { flex: 1, minWidth: 65, paddingHorizontal: 8 },
  previewValue: { fontSize: 13, lineHeight: 20 },
  previewHelp: { fontSize: 12, lineHeight: 18 },
});
