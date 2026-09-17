import React from 'react';
import { StyleSheet, Text, View } from 'react-native';
import { useAppTheme } from '../../design/ThemeProvider';
import { radii, spacing, typography } from '../../design/tokens';
import {
  VoiceConnectionState,
  VoiceSocketSnapshot,
} from '../../voice/VoiceSocket';

export type ConnectionStatusBadgeProps = {
  connection: VoiceConnectionState;
  session: VoiceSocketSnapshot['session'];
  statusText: string;
  isError?: boolean;
  testID?: string;
};

export function ConnectionStatusBadge({
  connection,
  session,
  statusText,
  isError = false,
  testID = 'voice-connection-status',
}: ConnectionStatusBadgeProps) {
  const { colors } = useAppTheme();

  const getDotColor = () => {
    if (isError || connection === 'failed') return colors.error;
    if (connection === 'degraded' || connection === 'reconnecting') {
      return colors.warning;
    }
    if (connection === 'connected' && session === 'ready') {
      return colors.success;
    }
    if (connection === 'connected') return colors.primary;
    return colors.disabled;
  };

  return (
    <View style={styles.container} testID={testID}>
      <View
        style={[
          styles.badge,
          {
            backgroundColor: isError
              ? colors.errorContainer
              : colors.surfaceLow,
            borderColor: isError ? colors.error : colors.borderSubtle,
          },
        ]}
      >
        <View style={[styles.dot, { backgroundColor: getDotColor() }]} />
        <Text
          numberOfLines={1}
          style={[
            styles.text,
            {
              color: isError ? colors.error : colors.textMuted,
              fontWeight: isError ? '600' : '500',
            },
          ]}
        >
          {statusText}
        </Text>
      </View>
    </View>
  );
}

const styles = StyleSheet.create({
  container: {
    alignItems: 'center',
    justifyContent: 'center',
    marginVertical: spacing.xs,
  },
  badge: {
    alignItems: 'center',
    borderRadius: radii.full,
    borderWidth: 1,
    flexDirection: 'row',
    maxWidth: '90%',
    minHeight: 32,
    paddingHorizontal: spacing.sm,
    paddingVertical: spacing.xxs,
  },
  dot: {
    borderRadius: radii.full,
    height: 8,
    marginRight: spacing.xs,
    width: 8,
  },
  text: {
    fontSize: typography.caption,
    letterSpacing: 0.2,
  },
});
