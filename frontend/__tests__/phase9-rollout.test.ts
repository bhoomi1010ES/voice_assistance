import {
  DEFAULT_VOICE_ROLLOUT_CONFIG,
  getVoiceRolloutConfig,
  isNativeBargeInAuthoritative,
  VoiceRolloutRuntimeGlobals,
} from '../src/config/voiceRollout';

describe('Phase 9 voice rollout flags', () => {
  test('defaults preserve native-authoritative behavior', () => {
    const config = getVoiceRolloutConfig({} as VoiceRolloutRuntimeGlobals);

    expect(config).toEqual(DEFAULT_VOICE_ROLLOUT_CONFIG);
    expect(isNativeBargeInAuthoritative(config)).toBe(true);
  });

  test('runtime overrides support the shadow rollout and reject malformed values', () => {
    const config = getVoiceRolloutConfig({
      __VOICE_ROLLOUT_FLAGS__: {
        nativeBargeInDetectorEnabled: true,
        legacyJsBargeInDetectorEnabled: true,
        duplexCommunicationRouteEnabled: 'false' as unknown as boolean,
        softwareAecMode: 'webrtc_aec3',
        earlyBargeInEnabled: 'true' as unknown as boolean,
      },
    } as unknown as VoiceRolloutRuntimeGlobals);

    expect(config.nativeBargeInDetectorEnabled).toBe(true);
    expect(config.legacyJsBargeInDetectorEnabled).toBe(true);
    expect(config.duplexCommunicationRouteEnabled).toBe(false);
    expect(config.softwareAecMode).toBe('WEBRTC_AEC3');
    expect(config.earlyBargeInEnabled).toBe(true);
    expect(isNativeBargeInAuthoritative(config)).toBe(false);
  });

  test('unknown software AEC mode falls back to platform', () => {
    const config = getVoiceRolloutConfig({
      __VOICE_ROLLOUT_FLAGS__: {
        softwareAecMode: 'unknown' as never,
      },
    } as unknown as VoiceRolloutRuntimeGlobals);

    expect(config.softwareAecMode).toBe('PLATFORM');
  });
});
