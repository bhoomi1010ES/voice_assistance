import React, { useEffect, useRef } from 'react';
import {
  Animated,
  Pressable,
  StyleSheet,
  Text,
  View,
} from 'react-native';
import { useAppTheme } from '../../design/ThemeProvider';
import { radii, shadows, spacing, typography } from '../../design/tokens';
import {
  VoiceConnectionState,
  VoiceSocketSnapshot,
} from '../../voice/VoiceSocket';

export type VoiceOrbViewProps = {
  busy: boolean;
  label: string;
  turnState: VoiceSocketSnapshot['turn'];
  connectionState: VoiceConnectionState;
  playbackState: VoiceSocketSnapshot['ttsPlaybackState'];
  disabled?: boolean;
  onPress: () => void;
  accessibilityLabel: string;
  accessibilityHint?: string;
  testID?: string;
};

export function VoiceOrbView({
  busy,
  label,
  turnState,
  connectionState,
  playbackState,
  disabled = false,
  onPress,
  accessibilityLabel,
  accessibilityHint = 'Activates the next voice session action',
  testID = 'voice-control',
}: VoiceOrbViewProps) {
  const { colors } = useAppTheme();
  const pulseAnim = useRef(new Animated.Value(1)).current;

  const isListening =
    turnState === 'recording' || turnState === 'speech_detected';
  const isThinking =
    turnState === 'committing' || turnState === 'waiting';
  const isSpeaking = playbackState === 'speaking';
  const isActive = isListening || isThinking || isSpeaking;

  useEffect(() => {
    if (isActive) {
      const pulseLoop = Animated.loop(
        Animated.sequence([
          Animated.timing(pulseAnim, {
            toValue: 1.08,
            duration: isListening ? 800 : 1200,
            useNativeDriver: true,
          }),
          Animated.timing(pulseAnim, {
            toValue: 1,
            duration: isListening ? 800 : 1200,
            useNativeDriver: true,
          }),
        ]),
      );
      pulseLoop.start();
      return () => {
        pulseLoop.stop();
        pulseAnim.setValue(1);
      };
    } else {
      pulseAnim.setValue(1);
    }
  }, [isActive, isListening, pulseAnim]);

  // Derive orb background and glow colors based on runtime state
  const getOrbColor = () => {
    if (connectionState === 'failed' || turnState === 'failed') {
      return colors.error;
    }
    if (isSpeaking) {
      return colors.secondary;
    }
    if (isListening) {
      return colors.primary;
    }
    if (isThinking) {
      return colors.primaryDark;
    }
    return colors.primary;
  };

  const orbColor = getOrbColor();

  return (
    <View style={styles.container}>
      {/* Outer ambient glow ring */}
      <Animated.View
        pointerEvents="none"
        style={[
          styles.ambientRing,
          {
            backgroundColor: colors.primaryContainer,
            opacity: isActive ? 0.45 : 0.2,
            transform: [{ scale: pulseAnim }],
          },
        ]}
      />

      {/* Middle halo ring */}
      <View
        pointerEvents="none"
        style={[
          styles.middleHalo,
          {
            borderColor: colors.borderSubtle,
            backgroundColor: colors.surfaceLow,
          },
        ]}
      />

      {/* Main interactive tactile orb button */}
      <Pressable
        accessibilityHint={accessibilityHint}
        accessibilityLabel={accessibilityLabel}
        accessibilityRole="button"
        disabled={disabled || busy}
        hitSlop={spacing.xs}
        onPress={onPress}
        style={({ pressed }) => [
          styles.orbCore,
          {
            backgroundColor: orbColor,
            opacity: pressed || busy ? 0.8 : 1,
          },
          shadows.lg,
        ]}
        testID={testID}
      >
        <Text style={styles.orbIcon}>◉</Text>
        <Text numberOfLines={2} style={styles.orbLabel}>
          {label}
        </Text>
      </Pressable>
    </View>
  );
}

const styles = StyleSheet.create({
  container: {
    alignItems: 'center',
    height: 180,
    justifyContent: 'center',
    marginVertical: spacing.sm,
    position: 'relative',
    width: '100%',
  },
  ambientRing: {
    borderRadius: radii.full,
    height: 172,
    position: 'absolute',
    width: 172,
  },
  middleHalo: {
    borderRadius: radii.full,
    borderWidth: 1,
    height: 148,
    position: 'absolute',
    width: 148,
  },
  orbCore: {
    alignItems: 'center',
    borderRadius: radii.full,
    elevation: 8,
    height: 124,
    justifyContent: 'center',
    padding: spacing.md,
    width: 124,
  },
  orbIcon: {
    color: '#FFFFFF',
    fontSize: 34,
    lineHeight: 38,
  },
  orbLabel: {
    color: '#FFFFFF',
    fontSize: typography.caption,
    fontWeight: '700',
    marginTop: 2,
    textAlign: 'center',
  },
});
