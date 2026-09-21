export type SoftwareAecMode = 'PLATFORM' | 'AUTO' | 'WEBRTC_AEC3';

export type VoiceRolloutConfig = {
  duplexCommunicationRouteEnabled: boolean;
  presentationTimedReferenceEnabled: boolean;
  nativeBargeInDetectorEnabled: boolean;
  legacyJsBargeInDetectorEnabled: boolean;
  platformAecEnabled: boolean;
  platformNsEnabled: boolean;
  softwareAecMode: SoftwareAecMode;
  earlyBargeInEnabled: boolean;
};

export type VoiceRolloutRuntimeGlobals = typeof globalThis & {
  __VOICE_ROLLOUT_FLAGS__?: Partial<VoiceRolloutConfig>;
};

export const DEFAULT_VOICE_ROLLOUT_CONFIG: VoiceRolloutConfig = {
  duplexCommunicationRouteEnabled: true,
  presentationTimedReferenceEnabled: true,
  nativeBargeInDetectorEnabled: true,
  legacyJsBargeInDetectorEnabled: false,
  platformAecEnabled: true,
  platformNsEnabled: true,
  softwareAecMode: 'PLATFORM',
  earlyBargeInEnabled: false,
};

function readBoolean(value: unknown, fallback: boolean): boolean {
  if (typeof value === 'boolean') {
    return value;
  }
  if (typeof value === 'string') {
    const normalized = value.trim().toLowerCase();
    if (normalized === 'true') return true;
    if (normalized === 'false') return false;
  }
  return fallback;
}

function readSoftwareAecMode(
  value: unknown,
  fallback: SoftwareAecMode,
): SoftwareAecMode {
  if (typeof value !== 'string') return fallback;
  const normalized = value.trim().toUpperCase();
  return normalized === 'PLATFORM' ||
    normalized === 'AUTO' ||
    normalized === 'WEBRTC_AEC3'
    ? normalized
    : fallback;
}

/**
 * Reads an optional runtime override used by internal/canary builds. The
 * production source of truth is the native Gradle BuildConfig; diagnostics
 * expose the native values so a JS/native mismatch is visible.
 */
export function getVoiceRolloutConfig(
  runtimeGlobals: VoiceRolloutRuntimeGlobals = globalThis,
): VoiceRolloutConfig {
  const override = runtimeGlobals.__VOICE_ROLLOUT_FLAGS__ ?? {};
  return {
    duplexCommunicationRouteEnabled: readBoolean(
      override.duplexCommunicationRouteEnabled,
      DEFAULT_VOICE_ROLLOUT_CONFIG.duplexCommunicationRouteEnabled,
    ),
    presentationTimedReferenceEnabled: readBoolean(
      override.presentationTimedReferenceEnabled,
      DEFAULT_VOICE_ROLLOUT_CONFIG.presentationTimedReferenceEnabled,
    ),
    nativeBargeInDetectorEnabled: readBoolean(
      override.nativeBargeInDetectorEnabled,
      DEFAULT_VOICE_ROLLOUT_CONFIG.nativeBargeInDetectorEnabled,
    ),
    legacyJsBargeInDetectorEnabled: readBoolean(
      override.legacyJsBargeInDetectorEnabled,
      DEFAULT_VOICE_ROLLOUT_CONFIG.legacyJsBargeInDetectorEnabled,
    ),
    platformAecEnabled: readBoolean(
      override.platformAecEnabled,
      DEFAULT_VOICE_ROLLOUT_CONFIG.platformAecEnabled,
    ),
    platformNsEnabled: readBoolean(
      override.platformNsEnabled,
      DEFAULT_VOICE_ROLLOUT_CONFIG.platformNsEnabled,
    ),
    softwareAecMode: readSoftwareAecMode(
      override.softwareAecMode,
      DEFAULT_VOICE_ROLLOUT_CONFIG.softwareAecMode,
    ),
    earlyBargeInEnabled: readBoolean(
      override.earlyBargeInEnabled,
      DEFAULT_VOICE_ROLLOUT_CONFIG.earlyBargeInEnabled,
    ),
  };
}

export const voiceRolloutConfig = getVoiceRolloutConfig();

export function isNativeBargeInAuthoritative(
  config: VoiceRolloutConfig,
): boolean {
  return (
    config.nativeBargeInDetectorEnabled &&
    !config.legacyJsBargeInDetectorEnabled
  );
}
