import React from 'react';
import {
  KeyboardAvoidingView,
  Modal,
  Platform,
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
            contentContainerStyle={styles.scrollContent}
            keyboardShouldPersistTaps="handled"
          >
            <View style={styles.header}>
              <Heading>
                {mode === 'create'
                  ? strings.tasks.createReminder
                  : strings.tasks.editReminder}
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
                placeholder="What should we remind you about?"
                placeholderTextColor={colors.textSubtle}
                style={[
                  styles.input,
                  {
                    color: colors.text,
                    borderColor: colors.border,
                    backgroundColor: colors.surface,
                  },
                ]}
                testID="reminder-title-input"
                value={draft.title}
              />
            </View>

            {/* Body Field */}
            <View style={styles.field}>
              <AppText style={[styles.fieldLabel, { color: colors.text }]}>
                {strings.tasks.bodyLabel}
              </AppText>
              <TextInput
                accessibilityLabel={strings.tasks.bodyLabel}
                multiline
                numberOfLines={3}
                onChangeText={body => onChange({ ...draft, body })}
                placeholder="Additional reminder details (optional)"
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
                testID="reminder-body-input"
                value={draft.body}
              />
            </View>

            {/* Date & Time Fields */}
            <View style={styles.field}>
              <AppText style={[styles.fieldLabel, { color: colors.text }]}>
                {strings.tasks.dateLabel} *
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
                testID="reminder-date-input"
                value={draft.date}
              />
              <View style={styles.quickDateRow}>
                <ActionButton
                  label={strings.tasks.tomorrow}
                  onPress={() =>
                    onChange({ ...draft, date: dateInputForOffset(1) })
                  }
                  testID="reminder-tomorrow"
                  variant="secondary"
                />
              </View>
            </View>

            <View style={styles.field}>
              <AppText style={[styles.fieldLabel, { color: colors.text }]}>
                {strings.tasks.timeLabel} *
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
                testID="reminder-time-input"
                value={draft.time}
              />
            </View>

            <View style={styles.field}>
              <AppText style={[styles.fieldLabel, { color: colors.text }]}>
                {strings.tasks.timezoneLabel} *
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
                testID="reminder-timezone-input"
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
                testID="reminder-schedule-preview"
              >
                {schedulePreview(draft.date, draft.time, draft.timezone)}
              </AppText>
              <AppText style={[styles.previewHelp, { color: colors.textMuted }]}>
                {strings.tasks.serverValidation}
              </AppText>
            </View>

            {/* Recurrence Selector */}
            <View style={styles.field}>
              <AppText style={[styles.fieldLabel, { color: colors.text }]}>
                {strings.tasks.recurrence}
              </AppText>
              <View style={styles.recurrenceRow}>
                {RECURRENCE_OPTIONS.map(choice => (
                  <ActionButton
                    key={choice}
                    label={choice}
                    onPress={() => onChange({ ...draft, recurrence: choice })}
                    testID={`recurrence-${choice}`}
                    variant={
                      draft.recurrence === choice ? 'primary' : 'secondary'
                    }
                  />
                ))}
              </View>
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
                testID="reminder-save"
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
  previewHelp: {
    fontSize: typography.caption,
    marginTop: spacing.xs,
  },
  recurrenceRow: {
    flexDirection: 'row',
    flexWrap: 'wrap',
    gap: spacing.xs,
  },
  modalActions: {
    flexDirection: 'row',
    flexWrap: 'wrap',
    gap: spacing.sm,
    justifyContent: 'flex-end',
    marginTop: spacing.md,
  },
});
