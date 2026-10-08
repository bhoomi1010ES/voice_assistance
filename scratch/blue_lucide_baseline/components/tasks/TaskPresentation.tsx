import React from 'react';
import {
  Pressable,
  StyleSheet,
  TextInput,
  TextInputProps,
  View,
} from 'react-native';
import { ActionButtonProps, AppText, Card } from '../ui/Primitives';
import { useAppTheme } from '../../design/ThemeProvider';
import { formatScheduledTime } from '../../tasks/scheduling';
import { radii, spacing } from '../../theme';

export const taskGradient = {
  backgroundColor: '#8B5CF6',
  experimental_backgroundImage:
    'linear-gradient(105deg, #7B61FF 0%, #CA48F1 46%, #FF7AAB 76%, #FFBA80 100%)',
};

type BadgeTone =
  | 'low'
  | 'normal'
  | 'high'
  | 'urgent'
  | 'pending'
  | 'scheduled'
  | 'completed'
  | 'sent'
  | 'failed'
  | 'cancelled'
  | 'voice';

export function TaskBadge({
  label,
  tone = 'normal',
  testID,
}: {
  label: string;
  tone?: BadgeTone;
  testID?: string;
}) {
  const { colors, mode } = useAppTheme();
  const palette: Record<BadgeTone, [string, string]> = {
    low: ['#EAF1FF', '#3479CC'],
    normal: ['#F5F3F6', '#65616C'],
    high: ['#FFEBF2', '#D72D68'],
    urgent: ['#FFE8E9', '#B92339'],
    pending: ['#FFF5E7', '#9C5B19'],
    scheduled: ['#FFF5E7', '#9C5B19'],
    completed: ['#E4F5ED', '#197B5D'],
    sent: ['#E8F1FF', '#286BC8'],
    failed: ['#FFEBF2', '#C82555'],
    cancelled: ['#FFEBF2', '#C82555'],
    voice: ['#F0E8FF', '#6C35D4'],
  };
  const [background, foreground] = palette[tone];
  return (
    <View
      style={[
        presentationStyles.badge,
        { backgroundColor: mode === 'dark' ? colors.surfaceHigh : background },
      ]}
      testID={testID}
    >
      <AppText
        style={[
          presentationStyles.badgeText,
          { color: mode === 'dark' ? colors.text : foreground },
        ]}
      >
        {label}
      </AppText>
    </View>
  );
}

export function TaskButton({
  label,
  variant = 'primary',
  destructive = false,
  style,
  ...props
}: ActionButtonProps & { destructive?: boolean }) {
  const { colors } = useAppTheme();
  return (
    <Pressable
      {...props}
      accessibilityRole="button"
      style={({ pressed }) => [
        presentationStyles.button,
        variant === 'primary'
          ? taskGradient
          : {
              backgroundColor:
                variant === 'quiet' ? 'transparent' : colors.surfaceMuted,
            },
        { opacity: props.disabled ? 0.45 : pressed ? 0.8 : 1 },
        style,
      ]}
    >
      <AppText
        style={[
          presentationStyles.buttonText,
          {
            color: destructive
              ? colors.error
              : variant === 'primary'
              ? '#FFFFFF'
              : colors.primary,
          },
        ]}
      >
        {label}
      </AppText>
    </Pressable>
  );
}

export function ScheduleLine({
  at,
  timezone,
}: {
  at: string | null;
  timezone: string;
}) {
  const { colors } = useAppTheme();
  return (
    <View style={presentationStyles.schedule}>
      <AppText
        style={[presentationStyles.scheduleText, { color: colors.textMuted }]}
      >
        ◷ {formatScheduledTime(at, timezone)}
      </AppText>
      <AppText
        style={[presentationStyles.scheduleText, { color: colors.textMuted }]}
      >
        ⌾ {timezone}
      </AppText>
    </View>
  );
}

export function EditorHeader({
  title,
  subtitle,
  onClose,
}: {
  title: string;
  subtitle: string;
  onClose: () => void;
}) {
  const { colors } = useAppTheme();
  return (
    <View style={presentationStyles.editorHeader}>
      <Pressable
        accessibilityLabel="Back"
        accessibilityRole="button"
        onPress={onClose}
        style={[presentationStyles.back, { backgroundColor: colors.surface }]}
      >
        <AppText
          style={[presentationStyles.backIcon, { color: colors.primary }]}
        >
          ‹
        </AppText>
      </Pressable>
      <View style={presentationStyles.grow}>
        <AppText style={presentationStyles.editorTitle}>{title}</AppText>
        <AppText
          style={[
            presentationStyles.editorSubtitle,
            { color: colors.textMuted },
          ]}
        >
          {subtitle}
        </AppText>
      </View>
    </View>
  );
}

export function EditorSummary({
  title,
  children,
  reminder = false,
}: {
  title: string;
  children: React.ReactNode;
  reminder?: boolean;
}) {
  const { colors } = useAppTheme();
  return (
    <Card style={presentationStyles.summary}>
      <View
        style={[
          presentationStyles.summaryIcon,
          { backgroundColor: colors.primaryContainer },
        ]}
      >
        <AppText
          style={[
            presentationStyles.summaryIconText,
            { color: colors.primary },
          ]}
        >
          {reminder ? '🔔' : '○'}
        </AppText>
      </View>
      <View style={presentationStyles.summaryContent}>
        <AppText style={presentationStyles.summaryTitle}>{title}</AppText>
        {children}
      </View>
    </Card>
  );
}

export function EditorField({
  label,
  ...props
}: TextInputProps & { label: string }) {
  const { colors } = useAppTheme();
  return (
    <View style={presentationStyles.field}>
      <AppText
        style={[presentationStyles.fieldLabel, { color: colors.textMuted }]}
      >
        {label}
      </AppText>
      <TextInput
        placeholderTextColor={colors.textSubtle}
        {...props}
        style={[
          presentationStyles.input,
          props.multiline ? presentationStyles.multiline : null,
          {
            color: colors.text,
            borderColor: colors.border,
            backgroundColor: colors.surface,
          },
          props.style,
        ]}
      />
    </View>
  );
}

export const presentationStyles = StyleSheet.create({
  backIcon: { fontSize: 30 },
  summaryIconText: { fontSize: 24 },
  badge: {
    borderRadius: radii.pill,
    paddingHorizontal: 10,
    paddingVertical: 5,
    maxWidth: '100%',
  },
  badgeText: {
    fontSize: 12,
    fontWeight: '600',
    lineHeight: 18,
    textTransform: 'capitalize',
  },
  button: {
    alignItems: 'center',
    justifyContent: 'center',
    minHeight: 44,
    paddingHorizontal: 14,
    paddingVertical: 10,
    borderRadius: 14,
    overflow: 'hidden',
  },
  buttonText: {
    fontSize: 14,
    fontWeight: '600',
    textAlign: 'center',
    lineHeight: 20,
  },
  schedule: {
    flexDirection: 'row',
    flexWrap: 'wrap',
    columnGap: 12,
    rowGap: 2,
  },
  scheduleText: { fontSize: 12, lineHeight: 18, flexShrink: 1 },
  editorHeader: {
    flexDirection: 'row',
    alignItems: 'center',
    gap: 14,
    paddingVertical: spacing.sm,
  },
  back: {
    width: 44,
    height: 48,
    alignItems: 'center',
    justifyContent: 'center',
    borderRadius: 16,
  },
  grow: { flex: 1 },
  editorTitle: {
    fontSize: 25,
    lineHeight: 32,
    fontWeight: '700',
    letterSpacing: -0.6,
  },
  editorSubtitle: { fontSize: 13, lineHeight: 20 },
  summary: {
    flexDirection: 'row',
    alignItems: 'flex-start',
    gap: 12,
    borderRadius: 22,
    padding: 16,
  },
  summaryIcon: {
    width: 42,
    height: 42,
    borderRadius: 24,
    alignItems: 'center',
    justifyContent: 'center',
  },
  summaryContent: { flex: 1, gap: 8 },
  summaryTitle: { fontSize: 18, lineHeight: 24, fontWeight: '700' },
  field: { flex: 1, gap: 7, minWidth: 0 },
  fieldLabel: { fontSize: 13, fontWeight: '600', lineHeight: 20 },
  input: {
    borderWidth: 1,
    borderRadius: 12,
    minHeight: 48,
    paddingHorizontal: 12,
    paddingVertical: 10,
    fontSize: 15,
  },
  multiline: { minHeight: 88, textAlignVertical: 'top' },
  formCard: { borderRadius: 22, padding: 16, gap: 18 },
  pairedFields: { flexDirection: 'row', gap: 12, alignItems: 'flex-start' },
  badges: { flexDirection: 'row', flexWrap: 'wrap', gap: 8 },
  scroll: { gap: 16, paddingBottom: 32 },
  preview: { borderRadius: 14, padding: 12, gap: 4 },
  footer: { gap: 8 },
});
