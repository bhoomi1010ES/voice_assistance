import React from 'react';
import {
  Pressable,
  PressableProps,
  StyleSheet,
  StyleProp,
  Text,
  TextProps,
  View,
  ViewProps,
  ViewStyle,
} from 'react-native';
import { SafeAreaView } from 'react-native-safe-area-context';
import { useAppTheme } from '../../design/ThemeProvider';
import { radii, spacing, typography } from '../../design/tokens';

export function Screen({ style, ...props }: ViewProps) {
  const { colors } = useAppTheme();
  return (
    <SafeAreaView
      style={[styles.safeArea, { backgroundColor: colors.background }]}
    >
      <View
        {...props}
        style={[styles.screen, { backgroundColor: colors.background }, style]}
      />
    </SafeAreaView>
  );
}

export function AppText({ style, ...props }: TextProps) {
  const { colors } = useAppTheme();
  return (
    <Text {...props} style={[styles.body, { color: colors.text }, style]} />
  );
}

export function Heading({ style, ...props }: TextProps) {
  const { colors } = useAppTheme();
  return (
    <Text {...props} style={[styles.heading, { color: colors.text }, style]} />
  );
}

export function Card({ style, ...props }: ViewProps) {
  const { colors } = useAppTheme();
  return (
    <View
      {...props}
      style={[
        styles.card,
        { backgroundColor: colors.surface, borderColor: colors.border },
        style,
      ]}
    />
  );
}

export type ActionButtonProps = Omit<PressableProps, 'style'> & {
  label: string;
  variant?: 'primary' | 'secondary' | 'quiet';
  style?: StyleProp<ViewStyle>;
};

export function ActionButton({
  label,
  style,
  variant = 'primary',
  ...props
}: ActionButtonProps) {
  const { colors } = useAppTheme();
  const isPrimary = variant === 'primary';
  const isQuiet = variant === 'quiet';

  return (
    <Pressable
      {...props}
      accessibilityRole="button"
      style={({ pressed }) => [
        styles.button,
        {
          backgroundColor: isPrimary
            ? colors.accent
            : isQuiet
            ? 'transparent'
            : colors.surfaceMuted,
          borderColor: isQuiet ? 'transparent' : colors.border,
          opacity: pressed ? 0.78 : props.disabled ? 0.5 : 1,
        },
        style,
      ]}
    >
      <Text
        style={{
          color: isPrimary ? colors.accentText : colors.text,
          fontSize: typography.body,
          fontWeight: '700',
        }}
      >
        {label}
      </Text>
    </Pressable>
  );
}

export function StatusBanner({
  children,
  tone = 'info',
}: {
  children: React.ReactNode;
  tone?: 'info' | 'error';
}) {
  const { colors } = useAppTheme();
  return (
    <View
      accessibilityRole="alert"
      style={[
        styles.banner,
        {
          backgroundColor:
            tone === 'error' ? `${colors.error}20` : colors.surfaceMuted,
          borderColor: tone === 'error' ? colors.error : colors.border,
        },
      ]}
    >
      <AppText
        style={{ color: tone === 'error' ? colors.error : colors.textMuted }}
      >
        {children}
      </AppText>
    </View>
  );
}

const styles = StyleSheet.create({
  safeArea: { flex: 1 },
  screen: { flex: 1, padding: spacing.lg },
  body: { fontSize: typography.body, lineHeight: 24 },
  heading: { fontSize: typography.heading, fontWeight: '700', lineHeight: 30 },
  card: { borderRadius: radii.md, borderWidth: 1, padding: spacing.lg },
  button: {
    alignItems: 'center',
    borderRadius: radii.md,
    borderWidth: 1,
    justifyContent: 'center',
    minHeight: 48,
    paddingHorizontal: spacing.lg,
    paddingVertical: spacing.sm,
  },
  banner: { borderRadius: radii.sm, borderWidth: 1, padding: spacing.md },
});
