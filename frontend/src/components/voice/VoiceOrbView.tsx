import { AppIcon } from '../ui/AppIcon';
import React, { useEffect, useRef } from 'react';
import { Animated, Pressable, StyleSheet, View } from 'react-native';
import { useAppTheme } from '../../design/ThemeProvider';
import { radii, shadows, spacing } from '../../design/tokens';
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

// Symmetrical height profiles for left and right audio waveforms
const LEFT_BAR_HEIGHTS = [12, 18, 15, 24, 38, 54, 42, 60, 48, 30, 20, 12];
const RIGHT_BAR_HEIGHTS = [12, 20, 30, 48, 60, 42, 54, 38, 24, 15, 18, 12];

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
  const waveAnim = useRef(new Animated.Value(1)).current;

  const isListening =
    turnState === 'recording' || turnState === 'speech_detected';
  const isThinking = turnState === 'committing' || turnState === 'waiting';
  const isSpeaking = playbackState === 'speaking';
  const isActive = isListening || isThinking || isSpeaking;

  useEffect(() => {
    if (isActive) {
      const pulseLoop = Animated.loop(
        Animated.sequence([
          Animated.timing(pulseAnim, {
            toValue: 1.06,
            duration: isListening ? 700 : 1100,
            useNativeDriver: true,
          }),
          Animated.timing(pulseAnim, {
            toValue: 1,
            duration: isListening ? 700 : 1100,
            useNativeDriver: true,
          }),
        ]),
      );
      const waveLoop = Animated.loop(
        Animated.sequence([
          Animated.timing(waveAnim, {
            toValue: 1.25,
            duration: 600,
            useNativeDriver: true,
          }),
          Animated.timing(waveAnim, {
            toValue: 0.85,
            duration: 600,
            useNativeDriver: true,
          }),
        ]),
      );
      pulseLoop.start();
      waveLoop.start();
      return () => {
        pulseLoop.stop();
        waveLoop.stop();
        pulseAnim.setValue(1);
        waveAnim.setValue(1);
      };
    } else {
      pulseAnim.setValue(1);
      waveAnim.setValue(1);
    }
  }, [isActive, isListening, pulseAnim, waveAnim]);

  return (
    <View style={styles.container}>
      {/* Horizontal audio waveforms in the background */}
      <View pointerEvents="none" style={styles.waveformContainer}>
        {/* Left waveform bars */}
        <View style={styles.waveformRow}>
          {LEFT_BAR_HEIGHTS.map((height, idx) => (
            <Animated.View
              key={`left-bar-${idx}`}
              style={[
                styles.waveformBar,
                {
                  height,
                  opacity: isActive ? 0.75 : 0.45,
                  transform: [
                    {
                      scaleY: isActive ? waveAnim : 1,
                    },
                  ],
                },
              ]}
            />
          ))}
        </View>

        {/* Center spacer matching the orb diameter */}
        <View style={styles.waveformSpacer} />

        {/* Right waveform bars */}
        <View style={styles.waveformRow}>
          {RIGHT_BAR_HEIGHTS.map((height, idx) => (
            <Animated.View
              key={`right-bar-${idx}`}
              style={[
                styles.waveformBar,
                {
                  height,
                  opacity: isActive ? 0.75 : 0.45,
                  transform: [
                    {
                      scaleY: isActive ? waveAnim : 1,
                    },
                  ],
                },
              ]}
            />
          ))}
        </View>
      </View>

      {/* Outer soft ambient glow ring */}
      <Animated.View
        pointerEvents="none"
        style={[
          styles.ambientRing,
          {
            backgroundColor: colors.orbRipple,
            transform: [{ scale: pulseAnim }],
          },
        ]}
      />

      {/* Middle soft halo ring */}
      <View
        pointerEvents="none"
        style={[
          styles.middleHalo,
          {
            backgroundColor: colors.orbGlow,
          },
        ]}
      />

      {/* Main interactive 3D glowing tactile orb button */}
      <Pressable
        accessibilityHint={accessibilityHint}
        accessibilityLabel={accessibilityLabel || label}
        accessibilityRole="button"
        disabled={disabled || busy}
        hitSlop={spacing.xs}
        onPress={onPress}
        style={({ pressed }) => [
          styles.orbCore,
          {
            opacity: pressed || busy ? 0.88 : 1,
          },
          shadows.lg,
        ]}
        testID={testID}
      >
        {/* Layer 1: Deep teal base */}
        <View style={styles.sphereBase} />

        {/* Layer 2: Cyan bottom-right glow */}
        <View style={styles.sphereCoralGlow} />

        {/* Layer 3: Teal ambient glow */}
        <View style={styles.sphereMagentaGlow} />

        {/* Layer 4: Specular gloss highlight crescent */}
        <View style={styles.sphereSpecularHighlight} />

        {/* Center white microphone icon */}
        <View style={styles.micContainer}>
          <AppIcon name="Mic" size={62} color="#FFFFFF" strokeWidth={2.3} />
        </View>
      </Pressable>
    </View>
  );
}

const styles = StyleSheet.create({
  container: {
    alignItems: 'center',
    height: 220,
    justifyContent: 'center',
    marginVertical: spacing.xs,
    position: 'relative',
    width: '100%',
  },
  waveformContainer: {
    alignItems: 'center',
    flexDirection: 'row',
    justifyContent: 'center',
    position: 'absolute',
    width: '100%',
    zIndex: 1,
  },
  waveformRow: {
    alignItems: 'center',
    flexDirection: 'row',
    gap: 3,
  },
  waveformSpacer: {
    width: 140,
  },
  waveformBar: {
    backgroundColor: '#35CED5',
    borderRadius: radii.full,
    width: 3.5,
  },
  ambientRing: {
    borderRadius: radii.full,
    height: 200,
    position: 'absolute',
    width: 200,
    zIndex: 2,
  },
  middleHalo: {
    borderRadius: radii.full,
    height: 172,
    position: 'absolute',
    width: 172,
    zIndex: 3,
  },
  orbCore: {
    borderWidth: 5,
    borderColor: '#35DEE4',
    alignItems: 'center',
    borderRadius: 72,
    elevation: 12,
    height: 144,
    justifyContent: 'center',
    overflow: 'hidden',
    position: 'relative',
    shadowColor: '#09A8AD',
    shadowOffset: { width: 0, height: 10 },
    shadowOpacity: 0.38,
    shadowRadius: 22,
    width: 144,
    zIndex: 4,
  },
  sphereBase: {
    backgroundColor: '#007789',
    bottom: 0,
    left: 0,
    position: 'absolute',
    right: 0,
    top: 0,
  },
  sphereMagentaGlow: {
    backgroundColor: '#007F97',
    borderRadius: 72,
    height: 144,
    left: 8,
    opacity: 0.85,
    position: 'absolute',
    top: -6,
    width: 144,
  },
  sphereCoralGlow: {
    backgroundColor: '#27E0E5',
    borderRadius: 60,
    bottom: -15,
    height: 120,
    opacity: 0.9,
    position: 'absolute',
    right: -15,
    width: 120,
  },
  sphereSpecularHighlight: {
    backgroundColor: 'rgba(184, 253, 255, 0.32)',
    borderRadius: radii.full,
    height: 52,
    left: 20,
    opacity: 0.85,
    position: 'absolute',
    top: 10,
    transform: [{ rotate: '-35deg' }],
    width: 90,
  },
  micContainer: {
    alignItems: 'center',
    justifyContent: 'center',
    zIndex: 10,
  },
});
