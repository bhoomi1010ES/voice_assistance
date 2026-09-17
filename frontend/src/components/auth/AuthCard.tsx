import React from 'react';
import { StyleProp, StyleSheet, View, ViewStyle } from 'react-native';
import { useAppTheme } from '../../design/ThemeProvider';
import { radii, shadows, spacing } from '../../design/tokens';

export type AuthCardProps = {
  children: React.ReactNode;
  style?: StyleProp<ViewStyle>;
};

export function AuthCard({ children, style }: AuthCardProps) {
  const { colors } = useAppTheme();

  return (
    <View
      style={[
        styles.card,
        {
          backgroundColor: colors.surface,
          borderColor: colors.borderSubtle,
        },
        shadows.md,
        style,
      ]}
    >
      {/* Subtle atmospheric ambient glow inside card corner */}
      <View
        pointerEvents="none"
        style={[
          styles.ambientGlow,
          { backgroundColor: colors.primaryContainer },
        ]}
      />
      {children}
    </View>
  );
}

const styles = StyleSheet.create({
  card: {
    borderRadius: radii.xl,
    borderWidth: 1,
    elevation: 4,
    overflow: 'hidden',
    padding: spacing.lg,
    position: 'relative',
    width: '100%',
  },
  ambientGlow: {
    borderRadius: radii.full,
    height: 120,
    opacity: 0.22,
    position: 'absolute',
    right: -40,
    top: -40,
    width: 120,
  },
});
