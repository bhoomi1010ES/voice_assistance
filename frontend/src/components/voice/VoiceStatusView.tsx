import React from 'react';
import { StyleSheet, Text, View } from 'react-native';
import { useAppTheme } from '../../design/ThemeProvider';
import { spacing, typography } from '../../design/tokens';
import { AppText } from '../ui/Primitives';

export type VoiceStatusViewProps = {
  primaryStatus: string;
  subStatus?: string;
  isActive?: boolean;
};

export function VoiceStatusView({
  primaryStatus,
  subStatus,
  isActive = false,
}: VoiceStatusViewProps) {
  const { colors } = useAppTheme();

  return (
    <View style={styles.container}>
      <View style={styles.primaryRow}>
        {isActive ? (
          <Text style={[styles.activeIndicator, { color: colors.primary }]}>
            ●
          </Text>
        ) : null}
        <AppText style={[styles.primaryText, { color: colors.text }]}>
          {primaryStatus}
        </AppText>
      </View>
      {subStatus ? (
        <AppText style={[styles.subText, { color: colors.textMuted }]}>
          {subStatus}
        </AppText>
      ) : null}
    </View>
  );
}

const styles = StyleSheet.create({
  container: {
    alignItems: 'center',
    gap: 4,
    justifyContent: 'center',
    marginVertical: spacing.xs,
    paddingHorizontal: spacing.md,
  },
  primaryRow: {
    alignItems: 'center',
    flexDirection: 'row',
    gap: spacing.xs,
  },
  activeIndicator: {
    fontSize: 10,
    lineHeight: 14,
  },
  primaryText: {
    fontSize: typography.heading,
    fontWeight: '700',
    letterSpacing: -0.2,
    textAlign: 'center',
  },
  subText: {
    fontSize: typography.bodySm,
    lineHeight: 20,
    textAlign: 'center',
  },
});
