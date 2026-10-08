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
import { radii, shadows, spacing, typography } from '../../design/tokens';

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

export function Subheading({ style, ...props }: TextProps) {
  const { colors } = useAppTheme();
  return (
    <Text
      {...props}
      style={[styles.subheading, { color: colors.textMuted }, style]}
    />
  );
}

export function Caption({ style, ...props }: TextProps) {
  const { colors } = useAppTheme();
  return (
    <Text
      {...props}
      style={[styles.caption, { color: colors.textSubtle }, style]}
    />
  );
}

export function Card({
  style,
  elevated = true,
  ...props
}: ViewProps & { elevated?: boolean }) {
  const { colors } = useAppTheme();
  return (
    <View
      {...props}
      style={[
        styles.card,
        {
          backgroundColor: colors.surface,
          borderColor: colors.border,
        },
        elevated ? shadows.sm : null,
        style,
      ]}
    />
  );
}

export type ActionButtonProps = Omit<PressableProps, 'style'> & {
  label: string;
  variant?: 'primary' | 'secondary' | 'quiet';
  pill?: boolean;
  style?: StyleProp<ViewStyle>;
};

export function ActionButton({
  label,
  style,
  variant = 'primary',
  pill = false,
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
        pill ? { borderRadius: radii.full } : null,
        {
          backgroundColor: isPrimary
            ? colors.accent
            : isQuiet
            ? 'transparent'
            : colors.surfaceMuted,
          borderColor: isQuiet ? 'transparent' : colors.border,
          opacity: pressed ? 0.82 : props.disabled ? 0.45 : 1,
        },
        isPrimary && !props.disabled ? shadows.sm : null,
        style,
      ]}
    >
      <Text
        style={{
          color: isPrimary
            ? colors.accentText
            : isQuiet
            ? colors.accent
            : colors.text,
          fontSize: typography.body,
          fontWeight: '600',
          letterSpacing: 0.2,
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
  tone?: 'info' | 'error' | 'success' | 'warning';
}) {
  const { colors } = useAppTheme();
  const getBannerColors = () => {
    switch (tone) {
      case 'error':
        return {
          bg: `${colors.error}18`,
          border: colors.error,
          text: colors.error,
        };
      case 'success':
        return {
          bg: `${colors.success}18`,
          border: colors.success,
          text: colors.success,
        };
      case 'warning':
        return {
          bg: `${colors.warning}18`,
          border: colors.warning,
          text: colors.warning,
        };
      default:
        return {
          bg: colors.surfaceMuted,
          border: colors.border,
          text: colors.textMuted,
        };
    }
  };

  const current = getBannerColors();

  return (
    <View
      accessibilityRole="alert"
      style={[
        styles.banner,
        {
          backgroundColor: current.bg,
          borderColor: current.border,
        },
      ]}
    >
      <AppText
        style={{
          color: current.text,
          fontSize: typography.label,
          fontWeight: '500',
        }}
      >
        {children}
      </AppText>
    </View>
  );
}

const styles = StyleSheet.create({
  safeArea: { flex: 1 },
  screen: { flex: 1, padding: spacing.lg },
  body: {
    fontSize: typography.body,
    lineHeight: 24,
  },
  heading: {
    fontSize: typography.heading,
    fontWeight: '700',
    lineHeight: 30,
    letterSpacing: -0.3,
  },
  subheading: {
    fontSize: typography.subheading,
    fontWeight: '600',
    lineHeight: 24,
  },
  caption: {
    fontSize: typography.caption,
    lineHeight: 16,
  },
  card: {
    borderRadius: radii.md,
    borderWidth: 1,
    padding: spacing.lg,
  },
  button: {
    alignItems: 'center',
    borderRadius: radii.md,
    borderWidth: 1,
    justifyContent: 'center',
    minHeight: 48,
    paddingHorizontal: spacing.lg,
    paddingVertical: spacing.sm + 2,
  },
  banner: {
    borderRadius: radii.sm,
    borderWidth: 1,
    padding: spacing.md,
  },
});
