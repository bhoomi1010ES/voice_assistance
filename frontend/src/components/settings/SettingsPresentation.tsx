import { AppIcon } from '../ui/AppIcon';
import React from 'react';
import { ScrollView, StyleSheet, View } from 'react-native';
import { AppText } from '../ui/Primitives';
import { useAppTheme } from '../../design/ThemeProvider';

export function SettingsCanvas({
  children,
  testID,
}: {
  children: React.ReactNode;
  testID?: string;
}) {
  const { colors, mode } = useAppTheme();
  return (
    <View
      style={[
        settingsPresentation.canvas,
        { backgroundColor: colors.background },
        mode === 'light' ? settingsPresentation.glow : null,
      ]}
      testID={testID}
    >
      <ScrollView contentContainerStyle={settingsPresentation.content}>
        {children}
      </ScrollView>
    </View>
  );
}

export function SettingsHero({
  title,
  subtitle,
  eyebrow,
}: {
  title: string;
  subtitle: string;
  eyebrow?: string;
}) {
  const { colors } = useAppTheme();
  return (
    <View style={settingsPresentation.hero}>
      <AppText accessibilityRole="header" style={settingsPresentation.title}>
        {title}
      </AppText>
      {eyebrow ? (
        <AppText
          style={[settingsPresentation.eyebrow, { color: colors.textMuted }]}
        >
          {eyebrow}
        </AppText>
      ) : null}
      <AppText
        style={[settingsPresentation.subtitle, { color: colors.textMuted }]}
      >
        {subtitle}
      </AppText>
    </View>
  );
}

export function SettingsIcon({
  kind,
  color,
}: {
  kind: 'profile' | 'device' | 'diagnostics' | 'connection' | 'sign-out';
  color?: string;
}) {
  const names = {
    profile: 'User',
    device: 'Laptop',
    diagnostics: 'AudioLines',
    connection: 'Activity',
    'sign-out': 'LogOut',
  } as const;
  return <AppIcon name={names[kind]} color={color} size={28} />;
}

export const settingsPresentation = StyleSheet.create({
  canvas: { flex: 1 },
  glow: {
    experimental_backgroundImage:
      'linear-gradient(135deg, #F6F8FC 0%, #F0F7FE 55%, #F4FBFC 100%)',
  },
  content: { gap: 20, padding: 16, paddingBottom: 32 },
  hero: { gap: 8, marginBottom: 2 },
  title: {
    fontSize: 30,
    lineHeight: 39,
    fontWeight: '800',
    letterSpacing: -0.7,
  },
  subtitle: { fontSize: 15, lineHeight: 23 },
  eyebrow: {
    fontSize: 12,
    fontWeight: '700',
    letterSpacing: 1.6,
    textTransform: 'uppercase',
    marginTop: 12,
  },
});
