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
  const { colors } = useAppTheme();
  const ink = color ?? colors.primary;
  return (
    <View accessible={false} style={iconStyles.frame}>
      {kind === 'profile' ? (
        <>
          <View style={[iconStyles.head, { borderColor: ink }]} />
          <View style={[iconStyles.shoulders, { borderColor: ink }]} />
        </>
      ) : kind === 'device' ? (
        <>
          <View style={[iconStyles.device, { borderColor: ink }]} />
          <View style={[iconStyles.base, { backgroundColor: ink }]} />
        </>
      ) : kind === 'sign-out' ? (
        <>
          <View style={[iconStyles.door, { borderColor: ink }]} />
          <AppText style={[iconStyles.arrow, { color: ink }]}>
            {'\u2192'}
          </AppText>
        </>
      ) : (
        <View style={iconStyles.bars}>
          {[10, 17, 25, 15].map((height, index) => (
            <View
              key={index}
              style={[
                iconStyles.bar,
                {
                  backgroundColor: ink,
                  height: kind === 'connection' ? 7 + index * 5 : height,
                },
              ]}
            />
          ))}
        </View>
      )}
    </View>
  );
}

export const settingsPresentation = StyleSheet.create({
  canvas: { flex: 1 },
  glow: {
    experimental_backgroundImage:
      'linear-gradient(135deg, #FAF8FF 0%, #FFF1F7 55%, #FFF8F1 100%)',
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

const iconStyles = StyleSheet.create({
  frame: {
    width: 30,
    height: 30,
    alignItems: 'center',
    justifyContent: 'center',
  },
  head: {
    width: 11,
    height: 11,
    borderWidth: 2,
    borderRadius: 8,
    position: 'absolute',
    top: 1,
  },
  shoulders: {
    width: 24,
    height: 13,
    borderWidth: 2,
    borderTopLeftRadius: 12,
    borderTopRightRadius: 12,
    borderBottomLeftRadius: 3,
    borderBottomRightRadius: 3,
    position: 'absolute',
    bottom: 1,
  },
  device: { width: 23, height: 21, borderWidth: 2, borderRadius: 3 },
  base: { width: 29, height: 3, borderRadius: 2, marginTop: 2 },
  bars: { flexDirection: 'row', alignItems: 'flex-end', gap: 3 },
  bar: { width: 4, borderRadius: 3 },
  door: {
    width: 17,
    height: 26,
    borderWidth: 2,
    borderRadius: 4,
    alignSelf: 'flex-start',
  },
  arrow: { position: 'absolute', right: -2, fontSize: 28, lineHeight: 30 },
});
