import React from 'react';
import { StyleSheet, Text, View } from 'react-native';
import { useAppTheme } from '../../design/ThemeProvider';
import { radii, spacing, typography } from '../../design/tokens';
import { AppText, Heading } from '../ui/Primitives';

export type AuthBrandHeaderProps = {
  title: string;
  subtitle: string;
};

export function AuthBrandHeader({ title, subtitle }: AuthBrandHeaderProps) {
  const { colors } = useAppTheme();

  return (
    <View style={styles.container}>
      {/* Brand Identity Orb */}
      <View style={styles.orbWrapper}>
        <View
          style={[
            styles.orbGlow,
            { backgroundColor: colors.primaryContainer },
          ]}
        />
        <View
          style={[
            styles.orbContainer,
            {
              backgroundColor: colors.surface,
              borderColor: colors.borderSubtle,
            },
          ]}
        >
          <Text style={[styles.brandGlyph, { color: colors.primary }]}>
            ◉
          </Text>
        </View>
      </View>

      {/* Title & Contextual Subtitle */}
      <Heading style={styles.title}>{title}</Heading>
      <AppText style={[styles.subtitle, { color: colors.textMuted }]}>
        {subtitle}
      </AppText>
    </View>
  );
}

const styles = StyleSheet.create({
  container: {
    alignItems: 'center',
    marginBottom: spacing.lg,
    paddingHorizontal: spacing.md,
  },
  orbWrapper: {
    alignItems: 'center',
    height: 72,
    justifyContent: 'center',
    marginBottom: spacing.md,
    position: 'relative',
    width: 72,
  },
  orbGlow: {
    borderRadius: radii.full,
    height: 68,
    opacity: 0.45,
    position: 'absolute',
    width: 68,
  },
  orbContainer: {
    alignItems: 'center',
    borderRadius: radii.xl,
    borderWidth: 1.5,
    elevation: 4,
    height: 56,
    justifyContent: 'center',
    shadowColor: '#000',
    shadowOffset: { width: 0, height: 2 },
    shadowOpacity: 0.08,
    shadowRadius: 6,
    width: 56,
  },
  brandGlyph: {
    fontSize: 28,
    lineHeight: 32,
  },
  title: {
    fontSize: typography.heading,
    fontWeight: '700',
    letterSpacing: -0.3,
    marginBottom: spacing.xxs,
    textAlign: 'center',
  },
  subtitle: {
    fontSize: typography.body,
    lineHeight: 22,
    maxWidth: 320,
    textAlign: 'center',
  },
});
